"""Drives requests through queued -> searching -> downloading | no
qualifying results | insufficient free space | failed -> complete | cancelled.

Two independent loops:
- `_process_queue`: one request at a time through the pipeline, guarded by
  an `asyncio.Lock` (the confirmed one-request-at-a-time architecture).
- `_watch_downloads`: polls qBittorrent for requests already added and
  sitting in "downloading", flipping them to "complete", or to "cancelled"
  if the torrent has disappeared from qBittorrent without this app doing it
  (a manual delete, external cleanup). This does *not* hold the pipeline
  lock — `search.start()` alone can already run to a ~55s ceiling, and the
  actual bittorrent transfer happens inside qBittorrent, independent of
  this backend, so watching it shouldn't block the next queued request.

"cancelled" is also reachable directly from the API (api.py's
`POST /api/requests/{id}/cancel`), which deletes the torrent (and its
files) from qBittorrent itself rather than waiting to notice it's gone.
Either path keeps the request row — "download history", not a queue —
per the "hidden, never unrecoverable" principle.

A third loop, `_watch_retention`, purges old *terminal* requests if a
retention policy is set in Settings — history that ages out, not a queue
entry left dangling.

A torrent disappearing from qBittorrent isn't always a deletion: with
qBittorrent's own "remove torrent after completion" option enabled, a
finished download vanishes the same way a cancelled one would. Before
concluding "cancelled", `_check_downloading` asks Plex (if linked) whether
the title actually made it into the library — "fail safe, not best
guess" applied to the one signal qBittorrent can no longer offer once the
torrent is gone.

A 5th loop, `_watch_shows` (Stage 12), is the standing-subscription
scheduler: for each "watching" show it fetches the current latest season
from TMDB, diffs against the `show_episodes` dedup ledger, and creates +
enqueues a normal episode `requests` row for every already-aired episode
not yet handled — reusing `_process_queue`'s single pipeline lock and
`_check_downloading`'s watcher completely unchanged, per the plan's
"no second lock, no distributed queue" call. `check_show()` is the same
code path api.py's `POST /api/shows` calls synchronously for the
immediate post-subscribe catch-up — "add show mid-season" and "scheduled
recheck" are one function, not two. Its wake-up interval is a real
Settings-panel value (`tv_settings.TVScheduleSettings.show_check_interval_hours`),
resolved fresh every cycle, same as pipeline settings elsewhere.

For an episode row specifically, `_check_downloading` additionally gates
on Stage 11's file organizer once the torrent reaches 100%: a successful
`organize_episode()` is what earns "complete" here, not just the torrent
finishing, and a `MediaOrganizerError` (e.g. the torrent evaporated before
this cycle even ran — see project.md's Stage 11 decision-log entry #3, a
real, still-open race with an aggressive `max_ratio` auto-remove) lands on
"downloaded, not filed" instead of a silent, wrong "complete".

A 6th loop, `_watch_episode_rechecks` (Stage 12.x), fixes a real gap the
scheduler above has on its own: `check_show()` marks an episode "handled"
in the dedup ledger the moment it's queued, regardless of whether the
download actually succeeds — so a "no qualifying results" episode would
otherwise never be retried, even after a real release later appears. Gated
behind `episode_recheck_enabled` (off by default — this is new, automatic,
unattended behavior), this loop periodically revisits every ledger entry
for every "watching" show and, per episode, either (a) retries a plain
search/add if nothing was ever successfully downloaded, or (b) peeks at
what's available now (`pipeline.find_best_episode_candidate`, no
`add_torrent` call) and only actually replaces an already-`"complete"`
episode if something genuinely scores higher than what's already in place.
A replacement adds the new torrent first and lets the existing organize-
on-complete path place it (`organize_episode()` is idempotent — it
replaces the file at the same deterministic path); only once that's
confirmed does `_organize_and_complete_episode` clean up the old torrent
it's replacing, via a `replaces_torrent_hash` marker on the new request —
never delete-then-hope-the-replacement-works.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from app import config, plex
from app.db import RequestStore, ShowEpisodeRow, ShowRow
from app.media_organizer import MediaOrganizerError, find_existing_episode_file, organize_episode, select_video_file
from app.pipeline import download, download_episode, find_best_episode_candidate
from app.pipeline_settings import resolve_pipeline_settings
from app.qbt import QBTClient
from app.tmdb import TMDBClient, TMDBError
from app.tv_resolve import resolve_show
from app.tv_settings import TVScheduleSettings, resolve_tv_settings

logger = logging.getLogger("app.worker")

# Pipeline statuses that map directly onto a terminal request status of the
# same name; anything else falls through to "failed" (see _run_one).
_DIRECT_TERMINAL_STATUSES = {"no qualifying results", "insufficient free space"}


def _request_label(row) -> str:
    """Log-friendly identifier — "{Show} S01E04" for an episode row,
    otherwise just the title, matching Stage 14's planned requests-list
    label change."""
    if row.media_type == "episode" and row.season_number is not None and row.episode_number is not None:
        return f"{row.title} S{row.season_number:02d}E{row.episode_number:02d}"
    return row.title


def _score_summary(score) -> str:
    if score is None:
        return "score=none"
    return (
        f"score=composite:{score.composite},resolution:{score.resolution_score},"
        f"source:{score.source_score},codec:{score.codec_score},"
        f"seeder:{score.seeder_score},container:{score.container_score}"
    )


def _result_summary(result) -> dict:
    """Audit-trail snapshot persisted alongside the request: which variant
    matched, the winning candidate, its score breakdown, and the torrent
    hash the download watcher tracks — everything Stage 2's CLI printed,
    now kept for the record per the "hidden, never unrecoverable" principle."""
    summary: dict = {
        "variant_used": result.variant_used,
        "candidates_considered": result.candidates_considered,
        "torrent_hash": result.torrent_hash,
    }
    if result.winner is not None:
        summary["winner"] = {
            "fileName": result.winner.get("fileName"),
            "engineName": result.winner.get("engineName"),
            "fileSize": result.winner.get("fileSize"),
            "nbSeeders": result.winner.get("nbSeeders"),
        }
    if result.score is not None:
        summary["score"] = {
            "resolution_score": result.score.resolution_score,
            "source_score": result.score.source_score,
            "codec_score": result.score.codec_score,
            "container_score": result.score.container_score,
            "seeder_score": result.score.seeder_score,
            "composite": result.score.composite,
        }
    if result.error is not None:
        summary["add_error"] = result.error
    return summary


class Worker:
    def __init__(self, store: RequestStore, tmdb: TMDBClient, qbt: QBTClient):
        self.store = store
        self.tmdb = tmdb
        self.qbt = qbt
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._pipeline_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []

    def enqueue(self, request_id: int) -> None:
        self.queue.put_nowait(request_id)

    async def start(self) -> None:
        recovered = await asyncio.to_thread(self.store.recover_interrupted)
        if recovered:
            logger.info("boot recovery: marked %d interrupted request(s) as failed", recovered)
        for request_id in await asyncio.to_thread(self.store.queued_request_ids):
            self.enqueue(request_id)
        self._tasks = [
            asyncio.create_task(self._process_queue(), name="worker-queue"),
            asyncio.create_task(self._watch_downloads(), name="worker-download-watch"),
            asyncio.create_task(self._watch_retention(), name="worker-retention"),
            asyncio.create_task(self._watch_shows(), name="worker-shows"),
            asyncio.create_task(self._watch_episode_rechecks(), name="worker-episode-rechecks"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []

    async def _process_queue(self) -> None:
        while True:
            request_id = await self.queue.get()
            async with self._pipeline_lock:
                await self._run_one(request_id)

    async def _run_one(self, request_id: int) -> None:
        row = await asyncio.to_thread(self.store.get_request, request_id)
        if row is None or row.status != "queued":
            return  # stale queue entry (e.g. re-enqueued across a restart)

        await asyncio.to_thread(self.store.update_status, request_id, "searching")
        try:
            settings = await asyncio.to_thread(resolve_pipeline_settings, self.store)
            if row.media_type == "episode":
                identity = await asyncio.to_thread(resolve_show, row.tmdb_id, self.tmdb)
                result = await asyncio.to_thread(
                    download_episode, identity, row.season_number, row.episode_number, self.qbt, settings
                )
            else:
                result = await asyncio.to_thread(download, row.tmdb_id, self.tmdb, self.qbt, settings)
        except Exception as exc:  # fail safe, not silent — never leave a row stuck
            logger.exception("request %d failed", request_id)
            await asyncio.to_thread(self.store.update_status, request_id, "failed", error_message=str(exc))
            return

        summary = _result_summary(result)
        logger.info(
            "request %d (%s) pipeline result=%s variant=%r candidates=%d %s",
            request_id,
            _request_label(row),
            result.status,
            result.variant_used,
            result.candidates_considered,
            _score_summary(result.score),
        )
        if result.status == "added":
            await asyncio.to_thread(self.store.update_status, request_id, "downloading", result=summary)
        elif result.status in _DIRECT_TERMINAL_STATUSES:
            await asyncio.to_thread(self.store.update_status, request_id, result.status, result=summary)
        elif result.status == "add failed":
            # Every fitting candidate across every variant failed to
            # actually add (see pipeline.py) — request status is "failed"
            # like any other failure, but `result` still carries the last
            # attempt's winner/score, unlike a bare exception would.
            await asyncio.to_thread(
                self.store.update_status,
                request_id,
                "failed",
                error_message=f"qBittorrent couldn't add any candidate release ({result.error})",
                result=summary,
            )
        else:
            await asyncio.to_thread(
                self.store.update_status,
                request_id,
                "failed",
                error_message=f"unexpected pipeline status: {result.status}",
                result=summary,
            )

    async def _watch_downloads(self) -> None:
        while True:
            await asyncio.sleep(config.DOWNLOAD_POLL_INTERVAL_SECONDS)
            try:
                await self._check_downloading()
            except Exception:
                logger.exception("download watch cycle failed")

    async def _check_downloading(self) -> None:
        for row in await asyncio.to_thread(self.store.list_requests, "downloading"):
            torrent_hash = (row.result or {}).get("torrent_hash")
            if not torrent_hash:
                continue  # couldn't be captured at add time — known gap, nothing to poll
            info = await asyncio.to_thread(self.qbt.torrent_info, torrent_hash)
            if info is None:
                if row.media_type == "episode":
                    # plex.has_in_library only supports a movie lookup
                    # (PlexClient.has_movie is type=1 only) — a TV
                    # equivalent is a real, documented gap, same style as
                    # this project's other named-not-solved gaps, so an
                    # episode's disappearance is reported as an unconfirmed
                    # removal rather than guessing at a check that doesn't
                    # fit the data.
                    message = "Removed from qBittorrent outside this app"
                    logger.info("request %d (%s) downloading -> cancelled (%s)", row.id, _request_label(row), message)
                    await asyncio.to_thread(self.store.update_status, row.id, "cancelled", error_message=message)
                    continue
                # Gone from qBittorrent without this app deleting it itself
                # (api.py's cancel route sets "cancelled" directly and never
                # reaches this branch). Two real causes look identical here:
                # someone deleted it, or qBittorrent's own "remove torrent
                # after completion" setting just cleaned up a *finished*
                # download. Ask Plex before assuming the worse one.
                found = await asyncio.to_thread(plex.has_in_library, self.store, row.title, row.release_year)
                if found:
                    logger.info("request %d (%s) downloading -> complete (confirmed via Plex)", row.id, row.title)
                    await asyncio.to_thread(self.store.update_status, row.id, "complete")
                else:
                    message = (
                        "Removed from qBittorrent outside this app"
                        if found is None
                        else "Removed from qBittorrent outside this app, and not found in Plex"
                    )
                    logger.info("request %d (%s) downloading -> cancelled (%s)", row.id, row.title, message)
                    await asyncio.to_thread(self.store.update_status, row.id, "cancelled", error_message=message)
            elif info.get("progress", 0) >= 1:
                if row.media_type == "episode":
                    await self._organize_and_complete_episode(row)
                else:
                    logger.info("request %d (%s) downloading -> complete", row.id, row.title)
                    await asyncio.to_thread(self.store.update_status, row.id, "complete")

    async def _organize_and_complete_episode(self, row) -> None:
        """Stage 11's organizer, finally wired to a real caller (Stage 12):
        an episode only earns "complete" once its file is actually placed
        in Plex's library layout, not merely once the torrent hits 100%.
        `resolve_show` is called fresh here rather than persisting a show
        identity on the request row — one extra TMDB call per completed
        episode, in exchange for not duplicating tv_resolve.py's logic."""
        torrent_hash = (row.result or {}).get("torrent_hash")
        label = _request_label(row)
        try:
            identity = await asyncio.to_thread(resolve_show, row.tmdb_id, self.tmdb)
            source_path = await asyncio.to_thread(select_video_file, self.qbt, torrent_hash)
            target_path = await asyncio.to_thread(
                organize_episode, identity, row.season_number, row.episode_number, source_path
            )
        except (MediaOrganizerError, TMDBError) as exc:
            logger.warning("request %d (%s) downloading -> downloaded, not filed (%s)", row.id, label, exc)
            await asyncio.to_thread(
                self.store.update_status, row.id, "downloaded, not filed", error_message=str(exc)
            )
            return
        logger.info("request %d (%s) downloading -> complete (organized to %s)", row.id, label, target_path)
        await asyncio.to_thread(self.store.update_status, row.id, "complete")

        # Stage 12.x's quality-upgrade recheck: the new file is safely in
        # place at the same deterministic library path (organize_episode()
        # already replaced whatever was there), so only *now* is it safe to
        # remove the torrent it superseded. Never the other way around —
        # deleting the old one first would risk leaving nothing in the
        # library if this add had failed instead. Best-effort: a cleanup
        # failure doesn't undo the "complete" status above, since the thing
        # that actually matters (the better file is in place) already
        # succeeded — an orphaned old torrent is a lesser, recoverable loose
        # end, not a reason to report this as broken.
        old_hash = (row.result or {}).get("replaces_torrent_hash")
        if old_hash:
            try:
                if await asyncio.to_thread(self.qbt.torrent_info, old_hash) is not None:
                    await asyncio.to_thread(self.qbt.delete_torrent, old_hash, delete_files=True)
                    logger.info("request %d (%s) upgrade complete — removed superseded torrent %s", row.id, label, old_hash)
            except Exception:
                logger.exception("request %d (%s) upgrade: couldn't clean up superseded torrent %s", row.id, label, old_hash)

    async def _watch_retention(self) -> None:
        while True:
            await asyncio.sleep(config.RETENTION_CLEANUP_INTERVAL_SECONDS)
            try:
                await self._cleanup_old_requests()
            except Exception:
                logger.exception("retention cleanup cycle failed")

    async def _cleanup_old_requests(self) -> None:
        settings = await asyncio.to_thread(self.store.get_settings)
        days = settings.get("request_retention_days")
        if not days:
            return  # no retention policy set — keep everything
        removed = await asyncio.to_thread(self.store.purge_requests_older_than, days)
        if removed:
            logger.info("retention cleanup: purged %d request(s) older than %d day(s)", removed, days)

    async def _watch_shows(self) -> None:
        while True:
            settings = await asyncio.to_thread(resolve_tv_settings, self.store)
            await asyncio.sleep(settings.show_check_interval_hours * 3600)
            try:
                await self._check_all_watching_shows()
            except Exception:
                logger.exception("show watch cycle failed")

    async def _check_all_watching_shows(self) -> None:
        for show in await asyncio.to_thread(self.store.list_shows, "watching"):
            try:
                created = await asyncio.to_thread(self.check_show, show)
                if created:
                    logger.info("show check: show %d (%s) queued %d new episode(s)", show.id, show.title, created)
            except Exception:
                logger.exception("show check crashed for show %d (%s)", show.id, show.title)

    def check_show(self, show: ShowRow) -> int:
        """Fetches the show's current latest season from TMDB, diffs it
        against the `show_episodes` dedup ledger, and creates + enqueues a
        normal episode request for every already-aired episode not yet
        handled. The *same* function backs both the scheduled recheck
        (`_check_all_watching_shows`, run every `show_check_interval_hours`
        per `tv_settings.TVScheduleSettings`) and api.py's `POST /api/shows`
        immediate post-subscribe catch-up —
        "add show mid-season" and "scheduled recheck" are one code path,
        per the plan. Only the latest season is ever checked (re-read fresh
        every call, so a new season is picked up automatically without any
        extra state); an older season resuming after a long hiatus is a
        named, accepted gap. Specials (season 0) are excluded for free —
        `number_of_seasons` doesn't count them, so they're never fetched.
        Sync (TMDB + sqlite, no asyncio) — callers run it via
        `asyncio.to_thread` or from a FastAPI sync route's own threadpool.

        Before creating a *download* request for an episode not yet in the
        ledger, checks whether a file for it already exists somewhere under
        `TV_LIBRARY_ROOT` (`find_existing_episode_file`) — a first-time
        subscribe to a show that already has episodes on disk (an earlier
        manual/CLI-only download, or anything else that bypassed this
        app's own request flow) must not re-search-and-re-add them. A found
        file still gets a `requests` row (created directly as `complete`,
        so it's visible in the same history as anything else) and a
        `show_episodes` marker — the ledger's job is knowing an episode is
        handled, not knowing how it got that way."""
        try:
            identity = resolve_show(show.tmdb_id, self.tmdb)
            show_data = self.tmdb.get_tv(show.tmdb_id)
            latest_season = show_data.get("number_of_seasons")
            episodes = self.tmdb.get_tv_season(show.tmdb_id, latest_season) if latest_season else []
        except TMDBError:
            logger.exception("show check: couldn't fetch TMDB data for show %d (%s)", show.id, show.title)
            return 0

        today = date.today().isoformat()
        created = 0
        for ep in episodes:
            episode_number = ep.get("episode_number")
            air_date = ep.get("air_date")
            if episode_number is None or not air_date or air_date > today:
                continue  # unaired, or TMDB has no air date on record yet
            if self.store.has_show_episode(show.id, latest_season, episode_number):
                continue

            existing_path = find_existing_episode_file(identity, latest_season, episode_number)
            if existing_path is not None:
                request_row = self.store.create_episode_request(
                    tmdb_id=show.tmdb_id,
                    show_id=show.id,
                    title=identity.title,
                    season_number=latest_season,
                    episode_number=episode_number,
                )
                self.store.update_status(
                    request_row.id,
                    "complete",
                    result={"note": "found already on disk, not downloaded by this app", "path": str(existing_path)},
                )
                self.store.add_show_episode(show.id, latest_season, episode_number, request_row.id)
                logger.info(
                    "show check: show %d (%s) S%02dE%02d already on disk (%s) — not downloading",
                    show.id,
                    show.title,
                    latest_season,
                    episode_number,
                    existing_path,
                )
                continue

            request_row = self.store.create_episode_request(
                tmdb_id=show.tmdb_id,
                show_id=show.id,
                title=identity.title,
                season_number=latest_season,
                episode_number=episode_number,
            )
            self.store.add_show_episode(show.id, latest_season, episode_number, request_row.id)
            self.enqueue(request_row.id)
            created += 1

        self.store.update_show_last_checked(show.id)
        return created

    # -- Stage 12.x: episode auto-recheck (retry a stuck episode, or look
    #    for a better release once one's already downloaded). --

    # Terminal statuses worth retrying — never "cancelled" (a deliberate
    # stop, whether from the API or from someone deleting the torrent
    # directly), and never the non-terminal ones (queued/searching/
    # downloading are already active, not stuck).
    _RECHECK_RETRY_STATUSES = frozenset({"no qualifying results", "insufficient free space", "failed", "downloaded, not filed"})

    async def _watch_episode_rechecks(self) -> None:
        while True:
            await asyncio.sleep(config.EPISODE_RECHECK_POLL_INTERVAL_SECONDS)
            try:
                await self._run_due_rechecks()
            except Exception:
                logger.exception("episode recheck cycle failed")

    async def _run_due_rechecks(self) -> None:
        settings = await asyncio.to_thread(resolve_tv_settings, self.store)
        if not settings.episode_recheck_enabled:
            return
        for show in await asyncio.to_thread(self.store.list_shows, "watching"):
            for episode_row in await asyncio.to_thread(self.store.list_show_episodes, show.id):
                if not self._recheck_is_due(episode_row, settings):
                    continue
                try:
                    await self._recheck_episode(show, episode_row, settings)
                except Exception:
                    logger.exception(
                        "recheck failed for show %d (%s) S%02dE%02d",
                        show.id,
                        show.title,
                        episode_row.season_number,
                        episode_row.episode_number,
                    )

    @staticmethod
    def _recheck_is_due(episode_row: ShowEpisodeRow, settings: TVScheduleSettings) -> bool:
        if settings.episode_recheck_max_attempts and episode_row.recheck_count >= settings.episode_recheck_max_attempts:
            return False
        last = episode_row.last_rechecked_at or episode_row.created_at
        due_at = datetime.fromisoformat(last) + timedelta(hours=settings.episode_recheck_interval_hours)
        return datetime.now(timezone.utc) >= due_at

    async def _recheck_episode(self, show: ShowRow, episode_row: ShowEpisodeRow, settings: TVScheduleSettings) -> None:
        """One recheck attempt for one episode: a plain retry if nothing's
        been successfully downloaded yet, or a peek-then-maybe-upgrade
        (`pipeline.find_best_episode_candidate`, no `add_torrent` call) if
        it has — only actually replacing a `"complete"` episode when
        something genuinely scores higher than what's already in place.
        Holds the same `_pipeline_lock` every other search/add operation
        does, so a recheck never races a queued request's own search/add."""
        current = await asyncio.to_thread(self.store.get_request, episode_row.request_id)
        if current is not None and current.status != "complete" and current.status not in self._RECHECK_RETRY_STATUSES:
            return  # actively in progress, or a deliberate cancel — don't burn an attempt on it

        identity = await asyncio.to_thread(resolve_show, show.tmdb_id, self.tmdb)
        pipeline_settings = await asyncio.to_thread(resolve_pipeline_settings, self.store)
        label = f"{show.title} S{episode_row.season_number:02d}E{episode_row.episode_number:02d}"

        async with self._pipeline_lock:
            if current is None or current.status != "complete":
                await self._recheck_retry(show, episode_row, identity, pipeline_settings, label)
            else:
                await self._recheck_upgrade(show, episode_row, current, identity, pipeline_settings, label)

    async def _recheck_retry(self, show, episode_row, identity, pipeline_settings, label: str) -> None:
        result = await asyncio.to_thread(
            download_episode, identity, episode_row.season_number, episode_row.episode_number, self.qbt, pipeline_settings
        )
        if result.status != "added":
            await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id)
            return
        new_row = await asyncio.to_thread(
            self.store.create_episode_request,
            show.tmdb_id,
            show.id,
            identity.title,
            episode_row.season_number,
            episode_row.episode_number,
        )
        await asyncio.to_thread(self.store.update_status, new_row.id, "downloading", None, _result_summary(result))
        await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id, new_row.id)
        logger.info("recheck: %s found a release on retry (attempt %d)", label, episode_row.recheck_count + 1)

    async def _recheck_upgrade(self, show, episode_row, current, identity, pipeline_settings, label: str) -> None:
        current_composite = ((current.result or {}).get("score") or {}).get("composite")
        best = await asyncio.to_thread(
            find_best_episode_candidate,
            identity,
            episode_row.season_number,
            episode_row.episode_number,
            self.qbt,
            pipeline_settings,
        )
        if best is None or (current_composite is not None and best[1].composite <= current_composite):
            await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id)
            return

        old_hash = (current.result or {}).get("torrent_hash")
        result = await asyncio.to_thread(
            download_episode, identity, episode_row.season_number, episode_row.episode_number, self.qbt, pipeline_settings
        )
        if result.status != "added":
            # The peek found something better a moment ago, but it didn't
            # actually land (e.g. qBittorrent rejected the add) — leave the
            # existing complete episode exactly as it is.
            await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id)
            return

        summary = _result_summary(result)
        if old_hash:
            summary["replaces_torrent_hash"] = old_hash
        new_row = await asyncio.to_thread(
            self.store.create_episode_request,
            show.tmdb_id,
            show.id,
            identity.title,
            episode_row.season_number,
            episode_row.episode_number,
        )
        await asyncio.to_thread(self.store.update_status, new_row.id, "downloading", None, summary)
        await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id, new_row.id)
        logger.info(
            "recheck: %s found a better release (composite %s -> %s), replacing once it finishes",
            label,
            current_composite,
            best[1].composite,
        )
