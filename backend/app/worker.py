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

A movie row gets the same organize-on-complete gate as an episode row
(`_organize_and_complete_movie`, mirroring `_organize_and_complete_episode`
exactly): `organize_movie()` has to actually rename the movie's folder
into Plex's layout before the request earns "complete". Previously movies
had no automatic organize step at all — `organize_movie()` was Stage
11-era CLI-only tooling, never wired into this watch loop until now.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import config, plex
from app.db import NON_TERMINAL_STATUSES, RequestStore, ShowEpisodeRow, ShowRow
from app.media_organizer import (
    MediaOrganizerError,
    find_existing_episode_file,
    organize_episode,
    organize_movie,
    organize_pack,
    select_video_file,
)
from app.pipeline import download, download_episode, download_pack, find_best_episode_candidate
from app.pipeline_settings import resolve_pipeline_settings
from app.qbt import QBTClient
from app.resolve import resolve
from app.tmdb import TMDBClient, TMDBError
from app.tv_resolve import ShowIdentity, aired_episode_numbers, resolve_show, season_is_complete
from app.tv_settings import TVScheduleSettings, resolve_tv_settings

logger = logging.getLogger("app.worker")

# Pipeline statuses that map directly onto a terminal request status of the
# same name; anything else falls through to "failed" (see _run_one).
_DIRECT_TERMINAL_STATUSES = {"no qualifying results", "insufficient free space"}


def _request_label(row) -> str:
    """Log-friendly identifier — "{Show} S01E04" for an episode row,
    "{Show} Season 01"/"{Show} Seasons 01-03"/"{Show} complete series" for
    a Stage 13/14.x bulk-pack row, otherwise just the title, matching
    Stage 14's planned requests-list label change."""
    if row.media_type == "episode" and row.season_number is not None and row.episode_number is not None:
        return f"{row.title} S{row.season_number:02d}E{row.episode_number:02d}"
    if row.media_type == "pack":
        if row.season_range_end is not None:
            scope = f"Seasons {row.season_number:02d}-{row.season_range_end:02d}"
        elif row.season_number is not None:
            scope = f"Season {row.season_number:02d}"
        else:
            scope = "complete series"
        return f"{row.title} {scope}"
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
    # Stage 13: PackDownloadResult carries a couple of fields the movie/
    # episode results don't (which literal query string actually matched) —
    # getattr-with-default keeps this one summary builder shared rather
    # than forking a pack-specific copy just for two extra keys.
    query_used = getattr(result, "query_used", None)
    if query_used is not None:
        summary["query_used"] = query_used
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
        # Fire-and-forget delayed source-cleanup tasks (Stage 13.x) — kept
        # in a set purely so nothing garbage-collects them mid-sleep; each
        # discards itself on completion via add_done_callback below.
        self._cleanup_tasks: set[asyncio.Task] = set()

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
            elif row.media_type == "pack":
                identity = await asyncio.to_thread(resolve_show, row.tmdb_id, self.tmdb)
                if row.season_range_end is not None:
                    scope = "season_range"
                elif row.season_number is not None:
                    scope = "season"
                else:
                    scope = "series"
                result = await asyncio.to_thread(
                    download_pack, identity, scope, self.qbt, settings, row.season_number, row.season_range_end
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
                if row.media_type in ("episode", "pack"):
                    # plex.has_in_library only supports a movie lookup
                    # (PlexClient.has_movie is type=1 only) — a TV
                    # equivalent is a real, documented gap, same style as
                    # this project's other named-not-solved gaps, so an
                    # episode's (or a pack's) disappearance is reported as
                    # an unconfirmed removal rather than guessing at a
                    # check that doesn't fit the data.
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
                elif row.media_type == "pack":
                    await self._organize_and_complete_pack(row)
                elif row.media_type == "movie":
                    await self._organize_and_complete_movie(row)
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
            source_path = await asyncio.to_thread(
                select_video_file, self.qbt, torrent_hash, config.QBIT_TV_SAVE_PATH, config.TV_LIBRARY_ROOT
            )
            target_path = await asyncio.to_thread(
                organize_episode, identity, row.season_number, row.episode_number, source_path
            )
        except (MediaOrganizerError, TMDBError, OSError) as exc:
            logger.warning("request %d (%s) downloading -> downloaded, not filed (%s)", row.id, label, exc)
            await asyncio.to_thread(
                self.store.update_status, row.id, "downloaded, not filed", error_message=str(exc)
            )
            return
        logger.info("request %d (%s) downloading -> complete (organized to %s)", row.id, label, target_path)
        await asyncio.to_thread(self.store.update_status, row.id, "complete")
        self._schedule_source_cleanup(torrent_hash, label, [target_path])

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

    async def _organize_and_complete_movie(self, row) -> None:
        """Movie equivalent of `_organize_and_complete_episode` — a movie
        only earns "complete" once `organize_movie()` has actually renamed
        its folder into Plex's `<Title> (<year>) {tmdb-<id>}` layout, not
        merely once the torrent hits 100%. Previously movies had no
        automatic organize step at all (`organize_movie()` was CLI-only,
        Stage 11-era manual tooling); this wires it into the same
        automatic watch loop TV episodes/packs already use, reusing the
        exact same organize-then-cleanup shape rather than a parallel
        one."""
        torrent_hash = (row.result or {}).get("torrent_hash")
        label = _request_label(row)
        try:
            identity = await asyncio.to_thread(resolve, row.tmdb_id, self.tmdb)
            source_path = await asyncio.to_thread(
                select_video_file, self.qbt, torrent_hash, config.QBIT_MOVIE_SAVE_PATH, config.MOVIE_LIBRARY_ROOT
            )
            target_path = await asyncio.to_thread(organize_movie, identity, source_path)
        except (MediaOrganizerError, TMDBError, OSError) as exc:
            logger.warning("request %d (%s) downloading -> downloaded, not filed (%s)", row.id, label, exc)
            await asyncio.to_thread(
                self.store.update_status, row.id, "downloaded, not filed", error_message=str(exc)
            )
            return
        logger.info("request %d (%s) downloading -> complete (organized to %s)", row.id, label, target_path)
        await asyncio.to_thread(self.store.update_status, row.id, "complete")
        self._schedule_source_cleanup(torrent_hash, label, [target_path])

    async def _organize_and_complete_pack(self, row) -> None:
        """Stage 13's fan-out point: a bulk season/complete-series pack row
        only earns "complete" once `organize_pack()` has actually placed
        its files — and only *then*, once the pack's real contents are
        known, does each episode it actually contains get its own normal
        `requests` row + `show_episodes` ledger marker. This deliberately
        differs from the plan text's literal "fan-out on add" phrasing:
        fanning out here, after organizing, rather than immediately after
        the torrent is added, is what lets a partially-matching pack
        (fewer episodes than TMDB says the season/series actually has)
        "accept what's actually there" — Stage 13's own open decision —
        without ever inventing a row for an episode the pack turns out not
        to contain. A `MediaOrganizerError` (torrent gone, or literally
        nothing recognizable inside it) lands the pack row itself on
        "downloaded, not filed", same vocabulary as a single-episode
        organize failure."""
        torrent_hash = (row.result or {}).get("torrent_hash")
        label = _request_label(row)
        try:
            identity = await asyncio.to_thread(resolve_show, row.tmdb_id, self.tmdb)
            placed = await asyncio.to_thread(organize_pack, identity, torrent_hash, self.qbt)
        except (MediaOrganizerError, TMDBError, OSError) as exc:
            logger.warning("pack request %d (%s) downloading -> downloaded, not filed (%s)", row.id, label, exc)
            await asyncio.to_thread(self.store.update_status, row.id, "downloaded, not filed", error_message=str(exc))
            return

        organized = 0
        for season, episode, target_path in placed:
            if await asyncio.to_thread(self.store.has_show_episode, row.show_id, season, episode):
                # Already tracked — e.g. the per-episode scheduler (Stage
                # 12) or an earlier pack already organized this exact
                # episode. Leave its existing requests/ledger row alone
                # rather than creating a duplicate audit trail for the
                # same file.
                continue
            ep_row = await asyncio.to_thread(
                self.store.create_episode_request, row.tmdb_id, row.show_id, identity.title, season, episode
            )
            await asyncio.to_thread(
                self.store.update_status,
                ep_row.id,
                "complete",
                result={
                    "note": f"organized from bulk download ({label})",
                    "path": str(target_path),
                    "torrent_hash": torrent_hash,
                },
            )
            await asyncio.to_thread(self.store.add_show_episode, row.show_id, season, episode, ep_row.id)
            organized += 1

        logger.info(
            "pack request %d (%s) downloading -> complete (organized %d of %d file(s))",
            row.id,
            label,
            organized,
            len(placed),
        )
        await asyncio.to_thread(self.store.update_status, row.id, "complete")
        self._schedule_source_cleanup(torrent_hash, label, [p for _, _, p in placed])

    def _schedule_source_cleanup(self, torrent_hash: str, label: str, target_paths: list) -> None:
        """Fires off `_cleanup_source_after_delay` without blocking the
        caller — organizing a request is done the moment its file(s) are
        placed; removing the now-redundant original is a lower-priority
        follow-up, not something worth holding up "complete" for."""
        task = asyncio.create_task(self._cleanup_source_after_delay(torrent_hash, label, target_paths))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _cleanup_source_after_delay(self, torrent_hash: str, label: str, target_paths: list) -> None:
        """Stage 13.x, at the user's explicit request: once a torrent's
        file(s) have been organized into Plex's library layout, remove the
        original torrent — qBittorrent queue entry *and* its own
        downloaded copy — after `config.SOURCE_CLEANUP_DELAY_SECONDS`,
        once a final re-check confirms every organized copy is genuinely
        still there. Safe by construction, not just by intent: a hardlinked
        organized copy shares the exact same underlying bytes as the
        torrent's own file (deleting one link never touches the data the
        other still points at), and the copy-fallback case already made a
        fully independent copy at organize time — either way, nothing
        unique is ever lost. Deletes via `qbt.delete_torrent()` rather than
        removing files itself, so qBittorrent — which already knows
        exactly which paths belong to this specific torrent — decides what
        "this torrent's files" means, never a path this code guesses at.

        A real, deliberate tradeoff, not a free lunch: this stops the
        torrent seeding earlier than the household's own qBittorrent-side
        seeding-time limit would have. Best-effort — if an organized copy
        has gone missing by the time the delay elapses, or qBittorrent has
        already removed the torrent itself, this skips cleanly rather than
        deleting anything or raising."""
        await asyncio.sleep(config.SOURCE_CLEANUP_DELAY_SECONDS)
        try:
            still_present = [await asyncio.to_thread(p.exists) for p in target_paths]
            if not all(still_present):
                logger.warning(
                    "source cleanup for torrent %s (%s) skipped — an organized copy is missing, not touching the original",
                    torrent_hash,
                    label,
                )
                return
            if await asyncio.to_thread(self.qbt.torrent_info, torrent_hash) is None:
                return  # already gone (this app's own doing, or otherwise) — nothing left to clean up
            await asyncio.to_thread(self.qbt.delete_torrent, torrent_hash, delete_files=True)
            logger.info("source cleanup: removed original torrent %s (%s) after organizing", torrent_hash, label)
        except Exception:
            logger.exception("source cleanup failed for torrent %s (%s)", torrent_hash, label)

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

    def _unhandled_episodes_for_season(
        self, show: ShowRow, identity: ShowIdentity, season_number: int, episodes: list[dict]
    ) -> list[int]:
        """Which of this season's already-aired episodes are genuinely
        still unhandled — not already in the `show_episodes` ledger, and
        not already sitting on disk (`find_existing_episode_file`; a match
        there is marked `complete` directly, as a side effect of this call,
        so a first-time subscribe to a show with episodes already on disk
        never re-searches-and-re-adds them). Factored out of
        `_check_show_season` so `check_show()`'s season-range detection
        (Stage 14.x) can reuse the exact same disk/ledger check when
        deciding how far a bundled range extends, without duplicating it."""
        aired = set(aired_episode_numbers(episodes))
        unhandled = []
        for episode_number in sorted(aired):
            if self.store.has_show_episode(show.id, season_number, episode_number):
                continue

            existing_path = find_existing_episode_file(identity, season_number, episode_number)
            if existing_path is not None:
                request_row = self.store.create_episode_request(
                    tmdb_id=show.tmdb_id,
                    show_id=show.id,
                    title=identity.title,
                    season_number=season_number,
                    episode_number=episode_number,
                )
                self.store.update_status(
                    request_row.id,
                    "complete",
                    result={"note": "found already on disk, not downloaded by this app", "path": str(existing_path)},
                )
                self.store.add_show_episode(show.id, season_number, episode_number, request_row.id)
                logger.info(
                    "show check: show %d (%s) S%02dE%02d already on disk (%s) — not downloading",
                    show.id,
                    show.title,
                    season_number,
                    episode_number,
                    existing_path,
                )
                continue

            unhandled.append(episode_number)

        return unhandled

    def _check_show_season(
        self,
        show: ShowRow,
        identity: ShowIdentity,
        season_number: int,
        episodes: list[dict],
        season_complete: bool,
        pack_due: bool,
    ) -> int:
        """One season's worth of `check_show()`'s own diff-then-download-
        or-mark-found logic, factored out so `full_backfill` can run it
        once per season instead of duplicating it. Returns the number of
        new *requests* actually created for this season (a pack counts as
        one, regardless of how many episodes it turns out to cover once
        organized).

        Three outcomes once at least one aired episode is genuinely
        unhandled (`_unhandled_episodes_for_season`):
        - `season_complete=False` (still airing) — per-episode requests,
          same as always; packs don't exist yet for an incomplete season.
        - `season_complete=True` and `pack_due=True` — a single season-pack
          request covers the whole season instead of one search per
          episode: an older, fully-aired season is realistically far more
          likely to still have one well-seeded pack release than
          well-seeded individual episodes, which tend to go cold once a
          show has moved on.
        - `season_complete=True` and `pack_due=False` (a pack was already
          tried — succeeded, still in flight, or failed and not yet due
          for a retry per `_should_attempt_pack()`) — nothing is created
          this call. Deliberately *not* a per-episode fallback: silently
          escalating to potentially many individual searches the moment a
          single pack attempt doesn't pan out would defeat the point of
          preferring packs for old content in the first place, and go
          against the same opt-in-only philosophy Stage 12.x's per-episode
          auto-recheck already established. The season stays alone until
          it's eligible for a pack retry (opted in, cooldown elapsed) or
          the user steps in manually (e.g. a fresh "Download this season"
          click, which tries again immediately regardless of this gate)."""
        unhandled = self._unhandled_episodes_for_season(show, identity, season_number, episodes)
        if not unhandled:
            return 0

        if season_complete:
            if not pack_due:
                return 0
            # Deliberately not added to show_episodes here — a pack row
            # only earns ledger entries once it's actually organized (see
            # _organize_and_complete_pack), same as a bulk-download
            # request. `_should_attempt_pack()` is what stops a failed
            # attempt from being silently re-tried every cycle.
            request_row = self.store.create_pack_request(
                tmdb_id=show.tmdb_id, show_id=show.id, title=identity.title, season_number=season_number
            )
            self.enqueue(request_row.id)
            logger.info(
                "show check: show %d (%s) season %d finished airing with %d unhandled episode(s) — "
                "queuing a season pack instead of per-episode searches",
                show.id,
                show.title,
                season_number,
                len(unhandled),
            )
            return 1

        created = 0
        for episode_number in unhandled:
            request_row = self.store.create_episode_request(
                tmdb_id=show.tmdb_id,
                show_id=show.id,
                title=identity.title,
                season_number=season_number,
                episode_number=episode_number,
            )
            self.store.add_show_episode(show.id, season_number, episode_number, request_row.id)
            self.enqueue(request_row.id)
            created += 1

        return created

    _PACK_DONE_STATUSES = {"complete"}

    def _should_attempt_pack(
        self,
        show: ShowRow,
        season_number: int | None,
        tv_settings: TVScheduleSettings,
        season_range_end: int | None = None,
    ) -> bool:
        """Whether check_show() should (re-)attempt a pack for this show at
        this exact scope (one season, a season range, or the complete
        series when `season_number` is None) right now:

        - Never tried before -> yes, always (the same "just try it once"
          behavior a full backfill's first sweep always had).
        - Already succeeded, or still actively in flight -> no, nothing to
          do (a second concurrent/duplicate attempt would just race the
          first one, or re-download something already complete).
        - The most recent attempt failed -> only a *retry*, gated exactly
          like Stage 12.x's existing per-episode auto-recheck: opt-in
          (`episode_recheck_enabled`), capped
          (`episode_recheck_max_attempts`, 0 = infinite), and cooled down
          (`episode_recheck_interval_hours` since that attempt). Reusing
          those settings rather than inventing pack-specific ones — "retry
          something that didn't work" is the same household preference
          either way. Without this gate, a persistently-unavailable pack
          would otherwise get silently re-queued by *every* scheduled
          recheck cycle forever, since (unlike a per-episode request,
          which claims its ledger slot the moment it's created) a pack
          only ever earns a `show_episodes` entry once it's actually
          organized — nothing else would ever mark a failed attempt
          "handled"."""
        attempts = self.store.list_pack_requests_for_show(show.id, season_number, season_range_end)
        if not attempts:
            return True
        latest = attempts[0]
        if latest.status in NON_TERMINAL_STATUSES or latest.status in self._PACK_DONE_STATUSES:
            return False
        if not tv_settings.episode_recheck_enabled:
            return False
        if tv_settings.episode_recheck_max_attempts and len(attempts) >= tv_settings.episode_recheck_max_attempts:
            return False
        due_at = datetime.fromisoformat(latest.updated_at) + timedelta(hours=tv_settings.episode_recheck_interval_hours)
        return datetime.now(timezone.utc) >= due_at

    def _detect_complete_unhandled_prefix(self, show: ShowRow, identity: ShowIdentity, latest_season: int) -> int:
        """How many seasons, starting from season 1, are both fully aired
        and still genuinely unhandled — the contiguous run a single real
        "S01-S0N"-style bundle would realistically cover. Walks seasons 1,
        2, 3... and stops at the first one that's either still airing
        (`season_is_complete` false) or already fully handled some other
        way (real range releases bundle from season 1, so a season 1
        that's already handled makes bundling pointless to even try).
        Returns 0 if season 1 itself doesn't qualify, or 1 if only season 1
        does — `check_show()` only acts on this when it's >= 2, since a
        single season is `_check_show_season`'s own case, not a range.

        Deliberately only ever called from `full_backfill` — unlike the
        show-ended complete-series check and the per-season pack
        preference (both driven by data already being fetched for other
        reasons on every call), detecting a range prefix needs fetching
        every season *before* the current one specifically to look for
        this, which would undo the ordinary scheduled recheck's whole
        "only ever touch the latest season" efficiency goal if it ran on
        every cycle. A range that only becomes detectable after the
        initial subscribe (e.g. season 2 finishes later) is a real,
        accepted gap symmetrical to this project's other "only checked
        once, at subscribe" ones — `_check_show_season`'s own per-season
        pack preference still catches each such season individually on
        the very next recheck, just one torrent at a time instead of
        bundled.

        Calling `_unhandled_episodes_for_season` here has the same
        find-on-disk/ledger side effects it always has, regardless of
        whether a range pack ends up firing — an episode genuinely on disk
        gets marked complete either way, and the fallback per-season sweep
        below simply sees it as already handled if a range pack isn't
        ultimately attempted."""
        prefix_end = 0
        for season_number in range(1, latest_season):
            try:
                episodes = self.tmdb.get_tv_season(show.tmdb_id, season_number)
            except TMDBError:
                break
            if not season_is_complete(episodes):
                break
            if not self._unhandled_episodes_for_season(show, identity, season_number, episodes):
                break
            prefix_end = season_number
        return prefix_end

    def check_show(self, show: ShowRow, full_backfill: bool = False) -> int:
        """Fetches the show's current latest season from TMDB, diffs it
        against the `show_episodes` dedup ledger, and creates + enqueues a
        normal episode request for every already-aired episode not yet
        handled. The *same* function backs both the scheduled recheck
        (`_check_all_watching_shows`, run every `show_check_interval_hours`
        per `tv_settings.TVScheduleSettings`) and api.py's `POST /api/shows`
        immediate post-subscribe catch-up —
        "add show mid-season" and "scheduled recheck" are one code path,
        per the plan. Specials (season 0) are excluded for free —
        `number_of_seasons` doesn't count them, so they're never fetched.
        Sync (TMDB + sqlite, no asyncio) — callers run it via
        `asyncio.to_thread` or from a FastAPI sync route's own threadpool.

        `full_backfill=True` — used only by `POST /api/shows`'s immediate
        post-subscribe call, never by the scheduled recheck — sweeps every
        season from 1 through the current latest, not just the latest, so
        a first-time subscribe to a show that's already several seasons in
        doesn't silently skip everything before the current season. Every
        subsequent scheduled recheck always passes the default `False` and
        keeps checking only the latest season — sweeping every past season
        again on every cycle forever would be repeated, pointless TMDB
        work for seasons with nothing left to discover.

        Three pack-preference tiers apply, each driven by a real signal
        rather than "is this the latest season" — every real torrent for
        old/finished content is far more likely to exist as a pack than as
        well-seeded individual episodes, and the more of the show one pack
        covers, the fewer separate downloads/searches it takes to get it:

        1. If the show itself has already ended (TMDB `status` is `Ended`
           or `Canceled`), a single complete-series pack is tried before
           anything else. When this fires, everything below is skipped
           entirely for this call (no point racing a dozen per-season/
           range searches against one that already covers all of them);
           if it doesn't pan out, tier 2/3 on some later call is exactly
           that fallback.
        2. Otherwise, on a `full_backfill` sweep specifically,
           `_detect_complete_unhandled_prefix()` looks for a genuine
           multi-season *range* — every season from 1 up to (but not
           including) the current one that's both finished airing and
           still genuinely unhandled. Two or more such seasons get one
           bundled range-pack request ("Reacher S01-S03" while season 4
           still airs) instead of several separate season packs; the
           per-season sweep below then only covers whatever the range
           didn't (season `prefix_end + 1` onward). Deliberately
           full-backfill-only — see that method's own docstring for why
           detecting a range on every ordinary recheck cycle would be too
           expensive to be worth it, and what still catches the same
           content anyway if this tier is skipped.
        3. Each season the sweep still reaches (every season for a full
           backfill that didn't consume a whole prefix via tier 2, or just
           the latest season for an ordinary recheck) is tested with
           `tv_resolve.season_is_complete()`: a season that's already
           finished airing gets a single season-pack request for whatever
           it still hasn't handled (see `_check_show_season`'s
           `pack_due`) instead of one per-episode search per missing
           episode. This tier applies to the *current* season too, the
           moment it finishes airing — including on an ordinary scheduled
           recheck, not only a first-ever full backfill, closing the gap
           that used to leave a since-finished season stuck on per-episode
           searches forever.

        All three tiers are guarded by `_should_attempt_pack()` — a pack
        is only ever tried once for free at each exact scope; a failed
        attempt only gets retried under the same opt-in/cooldown/max-
        attempts rule Stage 12.x's per-episode auto-recheck already uses,
        so a persistently-unavailable pack can't get silently re-queued
        every cycle forever.

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
        except TMDBError:
            logger.exception("show check: couldn't fetch TMDB data for show %d (%s)", show.id, show.title)
            return 0
        if not latest_season:
            return 0

        tv_settings = resolve_tv_settings(self.store)

        show_ended = show_data.get("status") in ("Ended", "Canceled")
        if show_ended and self._should_attempt_pack(show, None, tv_settings):
            request_row = self.store.create_pack_request(
                tmdb_id=show.tmdb_id, show_id=show.id, title=identity.title, season_number=None
            )
            self.enqueue(request_row.id)
            logger.info(
                "show check: show %d (%s) has ended — queuing a complete-series pack before any per-season fallback",
                show.id,
                show.title,
            )
            self.store.update_show_last_checked(show.id)
            return 1

        seasons_to_check = range(1, latest_season + 1) if full_backfill else [latest_season]
        created = 0

        # Stage 14.x: a genuine multi-season *range* pack ("Reacher
        # S01-S03" while a later season still airs) — tried only on a
        # full backfill (see _detect_complete_unhandled_prefix's own note
        # on why this never runs on an ordinary recheck), and only when
        # there are at least two seasons in it; a single season is
        # _check_show_season's own single-season-pack case, not a range.
        if full_backfill and not show_ended and latest_season > 1:
            prefix_end = self._detect_complete_unhandled_prefix(show, identity, latest_season)
            if prefix_end >= 2 and self._should_attempt_pack(show, 1, tv_settings, season_range_end=prefix_end):
                request_row = self.store.create_pack_request(
                    tmdb_id=show.tmdb_id, show_id=show.id, title=identity.title,
                    season_number=1, season_range_end=prefix_end,
                )
                self.enqueue(request_row.id)
                logger.info(
                    "show check: show %d (%s) seasons 1-%d have finished airing with unhandled episodes — "
                    "queuing one season-range pack instead of per-season searches",
                    show.id,
                    show.title,
                    prefix_end,
                )
                created += 1
                seasons_to_check = range(prefix_end + 1, latest_season + 1)

        for season_number in seasons_to_check:
            try:
                episodes = self.tmdb.get_tv_season(show.tmdb_id, season_number)
            except TMDBError:
                logger.exception(
                    "show check: couldn't fetch season %d for show %d (%s)", season_number, show.id, show.title
                )
                continue
            complete = season_is_complete(episodes)
            pack_due = complete and self._should_attempt_pack(show, season_number, tv_settings)
            created += self._check_show_season(show, identity, season_number, episodes, complete, pack_due)

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
