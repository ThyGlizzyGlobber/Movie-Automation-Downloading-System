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
confirmed does the old torrent it's replacing get folded into the same
durable cleanup sweep as the new one, via a `replaces_torrent_hash` marker
on the new request — never delete-then-hope-the-replacement-works.

A 7th loop, `_watch_source_cleanup` (Stage 13.x, rebuilt after a real bug),
removes a now-redundant original torrent once its file(s) are safely
organized, after `config.SOURCE_CLEANUP_DELAY_SECONDS`. The original
version fired this off as an in-memory `asyncio.create_task` the instant
organizing succeeded — simple, but silently lost the work forever if the
backend restarted, or the one-shot delete itself failed, anywhere in that
window; found live when a user-side qBittorrent action collided with the
window for one real episode. Rebuilt to be durable like every other loop
here: `mark_organized()` persists what needs cleaning up (which torrent
hash(es), which organized path(s) to re-verify first) straight onto the
request row, `_watch_source_cleanup` re-derives its work fresh from the
database every cycle, and a failed attempt is retried on the next sweep
rather than abandoned. Once a row's cleanup is marked done — success, or a
deliberate, permanent skip (its organized copy has since gone missing) —
it's never revisited again.

A movie row gets the same organize-on-complete gate as an episode row
(`_organize_and_complete_movie`, mirroring `_organize_and_complete_episode`
exactly): `organize_movie()` has to actually rename the movie's folder
into Plex's layout before the request earns "complete". Previously movies
had no automatic organize step at all — `organize_movie()` was Stage
11-era CLI-only tooling, never wired into this watch loop until now.
"""

import asyncio

import dataclasses
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import config, plex
from app.db import NON_TERMINAL_STATUSES, RequestStore, ShowEpisodeRow, ShowRow
from app.media_organizer import (
    MediaOrganizerError,
    NoVideoFileError,
    find_existing_episode_file,
    organize_episode,
    organize_movie,
    organize_pack,
    select_video_file,
)
from app.pipeline import download, download_episode, download_pack, find_best_episode_candidate, same_release
from app.pipeline_settings import resolve_pipeline_settings
from app.qbt import QBTClient, QBTError
from app.resolve import resolve
from app.tmdb import TMDBClient, TMDBError
from app.tv_resolve import ShowIdentity, aired_episode_numbers, resolve_show, season_is_complete
from app.tvmaze import TVMazeClient, season_airstamps
from app.tv_settings import TVScheduleSettings, resolve_tv_settings

logger = logging.getLogger("app.worker")

# Pipeline statuses that map directly onto a terminal request status of the
# same name; anything else falls through to "failed" (see _run_one).
_DIRECT_TERMINAL_STATUSES = {"no qualifying results", "insufficient free space"}

# How long a "downloading" row with no torrent on record gets to turn up
# in qBittorrent before it's marked failed rather than left spinning.
_UNTRACKED_GRACE = timedelta(hours=1)


def _stalled_with_dead_swarm(info: dict) -> bool:
    """Whether a torrent qBittorrent still holds has downloaded nothing,
    from nobody, for long enough that it isn't going to.

    Deliberately narrow — all three of zero progress (not "slow"), zero
    connected seeds, and past the grace period. A torrent that is merely
    throttled, queued behind others, or still finding peers fails at
    least one of them, so the only thing this catches is a pick with no
    swarm behind it at all."""
    if (info.get("progress") or 0) > 0:
        return False
    if (info.get("num_seeds") or 0) > 0:
        return False
    # qBittorrent's own clock for this torrent. Absent or nonsensical
    # (an adopted untracked torrent, a client that didn't report it)
    # means we can't say how long it's been like this — so we don't.
    added_on = info.get("added_on") or 0
    if added_on <= 0:
        return False
    return (time.time() - added_on) >= config.STALL_GRACE_SECONDS


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
    def __init__(self, store: RequestStore, tmdb: TMDBClient, qbt: QBTClient, tvmaze: TVMazeClient | None = None):
        self.store = store
        self.tmdb = tmdb
        self.qbt = qbt
        # Episode release times, which TMDB has no field for. Optional so
        # the existing three-argument construction keeps working; its own
        # lookups are best effort and fall back to TMDB's date.
        self.tvmaze = tvmaze or TVMazeClient()
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        # Bounds how many requests are being searched at once, shared by
        # the queue consumers and the recheck loop so the two together
        # can't exceed it. Replaces the single lock that used to wrap a
        # whole request — see config.SEARCH_CONCURRENCY for why that was
        # the wrong thing to serialise, and pipeline._ADD_LOCK for the
        # part that genuinely still is.
        self._search_slots = asyncio.Semaphore(config.SEARCH_CONCURRENCY)
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
            *(
                asyncio.create_task(self._process_queue(), name=f"worker-queue-{n}")
                for n in range(config.SEARCH_CONCURRENCY)
            ),
            asyncio.create_task(self._watch_downloads(), name="worker-download-watch"),
            asyncio.create_task(self._watch_retention(), name="worker-retention"),
            asyncio.create_task(self._watch_shows(), name="worker-shows"),
            asyncio.create_task(self._watch_episode_rechecks(), name="worker-episode-rechecks"),
            asyncio.create_task(self._watch_source_cleanup(), name="worker-source-cleanup"),
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
        """One consumer. `start()` runs SEARCH_CONCURRENCY of these, so
        several requests are searched at once; the semaphore is what
        actually bounds it, since the recheck loop draws on the same
        slots."""
        while True:
            request_id = await self.queue.get()
            async with self._search_slots:
                await self._run_one(request_id)

    async def _run_one(self, request_id: int) -> None:
        row = await asyncio.to_thread(self.store.get_request, request_id)
        if row is None or row.status != "queued":
            return  # stale queue entry (e.g. re-enqueued across a restart)

        await asyncio.to_thread(self.store.update_status, request_id, "searching")
        try:
            settings = await asyncio.to_thread(resolve_pipeline_settings, self.store)
            if row.min_resolution:
                # A per-row floor override (requests.min_resolution). Nothing
                # in the app sets it any more — a request always means "the
                # best copy that exists" — but a row that carries one is
                # still honoured.
                settings = dataclasses.replace(settings, min_resolution=row.min_resolution)
            # Stage 15: torrents explicitly rejected as genuinely defective
            # on a prior attempt for this same movie/show — excluded from
            # this fresh search so it never re-selects the exact same bad
            # release (see api.py's POST /api/requests/{id}/reject).
            rejected_hashes = await asyncio.to_thread(self.store.get_rejected_torrent_hashes, row.tmdb_id)
            if row.media_type == "episode":
                identity = await asyncio.to_thread(resolve_show, row.tmdb_id, self.tmdb)
                result = await asyncio.to_thread(
                    download_episode,
                    identity,
                    row.season_number,
                    row.episode_number,
                    self.qbt,
                    settings,
                    rejected_hashes,
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
                    download_pack,
                    identity,
                    scope,
                    self.qbt,
                    settings,
                    row.season_number,
                    row.season_range_end,
                    rejected_hashes,
                )
            else:
                rejected_releases = await asyncio.to_thread(self.store.get_rejected_releases, row.tmdb_id)
                result = await asyncio.to_thread(
                    download, row.tmdb_id, self.tmdb, self.qbt, settings, rejected_hashes, rejected_releases
                )
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
            if row.media_type == "pack" and result.status == "no qualifying results":
                await self._fall_back_from_pack(row, result.identity, getattr(result, "note", None))
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

    async def _fall_back_from_pack(self, row, identity: ShowIdentity, note: str | None = None) -> None:
        """A pack request that found nothing steps down one level: a
        whole-series (or season-range) request becomes one request per
        season, and a season request becomes one request per aired
        episode. Confirmed live 2026-09-17 (PEN15): the series pack didn't
        exist, but each season had a well-seeded pack of its own — going
        straight to single episodes would have meant dozens of poorly
        seeded downloads instead of two good ones."""
        if row.season_number is None or row.season_range_end is not None:
            await self._fall_back_to_seasons(row, identity, note)
        else:
            await self._fall_back_to_episodes(row, identity)

    async def _fall_back_to_seasons(self, row, identity: ShowIdentity, note: str | None = None) -> None:
        """One season-pack request per season in the failed request's
        scope, skipping seasons that have nothing aired, that are already
        fully in the ledger, or that already have a pack in flight or
        done. Each of those can still drop to episodes on its own."""
        show = await asyncio.to_thread(self.store.get_show, row.show_id)
        if show is None:
            return
        if row.season_range_end is not None:
            seasons = list(range(row.season_number, row.season_range_end + 1))
        else:
            try:
                show_data = await asyncio.to_thread(self.tmdb.get_tv, row.tmdb_id)
            except TMDBError:
                logger.exception("pack fallback: couldn't fetch TMDB data for %s", _request_label(row))
                return
            seasons = list(range(1, (show_data.get("number_of_seasons") or 0) + 1))

        tv_settings = await asyncio.to_thread(resolve_tv_settings, self.store)
        on_plex = await asyncio.to_thread(self._episodes_on_plex, identity)
        created: list[int] = []
        for season_number in seasons:
            try:
                episodes = await asyncio.to_thread(self.tmdb.get_tv_season, row.tmdb_id, season_number)
            except TMDBError:
                logger.exception("pack fallback: couldn't fetch season %d for %s", season_number, _request_label(row))
                continue
            stamps = await asyncio.to_thread(self._season_airstamps, identity, season_number)
            aired = aired_episode_numbers(
                episodes, buffer_hours=tv_settings.episode_air_buffer_hours, airstamps=stamps
            )
            if not aired:
                continue
            had = []
            for e in aired:
                if (season_number, e) in on_plex:
                    if not await asyncio.to_thread(self.store.has_live_show_episode, show.id, season_number, e):
                        await asyncio.to_thread(self._mark_found, show, identity, season_number, e, "already on Plex, not downloaded by this app")
                    had.append(True)
                else:
                    had.append(await asyncio.to_thread(self.store.has_live_show_episode, show.id, season_number, e))
            if all(had):
                continue
            attempts = await asyncio.to_thread(self.store.list_pack_requests_for_show, show.id, season_number, None)
            if attempts and (attempts[0].status in NON_TERMINAL_STATUSES or attempts[0].status in self._PACK_DONE_STATUSES):
                continue
            season_row = await asyncio.to_thread(
                self.store.create_pack_request,
                show.tmdb_id,
                show.id,
                identity.title,
                season_number,
                None,
                row.requested_by_plex_id,
                row.requested_by_username,
                row.min_resolution,
                None,
                show.poster_path or identity.poster_path,
            )
            created.append(season_row.id)

        scope = "series pack" if row.season_number is None else "season-range pack"
        lead = f"{note}, so" if note else f"No {scope} found, so"
        if created:
            n = len(created)
            message = f"{lead} its {n} season{'s were' if n != 1 else ' was'} requested one at a time."
        else:
            message = f"{lead} nothing was added: every season is already requested or on Plex."
        await asyncio.to_thread(self.store.update_status, row.id, "no qualifying results", error_message=message)
        logger.info("pack fallback: %s -> %d per-season request(s)", _request_label(row), len(created))
        for season_id in created:
            self.enqueue(season_id)

    async def _fall_back_to_episodes(self, row, identity: ShowIdentity) -> None:
        """A season request that found no pack is re-issued as one request
        per aired episode. Plenty of older or smaller shows only ever
        circulate as single episodes. Episodes already in the ledger
        (handled by an earlier request, or found on disk) are left alone;
        the new rows inherit the request's resolution floor, and
        the pack row keeps its "no match" but says what happened next."""
        show = await asyncio.to_thread(self.store.get_show, row.show_id)
        if show is None:
            return
        seasons = [row.season_number]

        tv_settings = await asyncio.to_thread(resolve_tv_settings, self.store)
        on_plex = await asyncio.to_thread(self._episodes_on_plex, identity)
        created: list[int] = []
        for season_number in seasons:
            try:
                episodes = await asyncio.to_thread(self.tmdb.get_tv_season, row.tmdb_id, season_number)
            except TMDBError:
                logger.exception("pack fallback: couldn't fetch season %d for %s", season_number, _request_label(row))
                continue
            stamps = await asyncio.to_thread(self._season_airstamps, identity, season_number)
            for episode_number in aired_episode_numbers(
                episodes, buffer_hours=tv_settings.episode_air_buffer_hours, airstamps=stamps
            ):
                if await asyncio.to_thread(self.store.has_live_show_episode, show.id, season_number, episode_number):
                    continue
                if (season_number, episode_number) in on_plex:
                    await asyncio.to_thread(self._mark_found, show, identity, season_number, episode_number, "already on Plex, not downloaded by this app")
                    continue
                episode_row = await asyncio.to_thread(
                    self.store.create_episode_request,
                    show.tmdb_id,
                    show.id,
                    identity.title,
                    season_number,
                    episode_number,
                    show.poster_path or identity.poster_path,
                    row.min_resolution,
                )
                await asyncio.to_thread(self.store.add_show_episode, show.id, season_number, episode_number, episode_row.id)
                created.append(episode_row.id)

        if created:
            n = len(created)
            message = f"No season pack found, so its {n} episode{'s were' if n != 1 else ' was'} requested one by one."
        else:
            message = "No season pack found, and every aired episode is already requested."
        await asyncio.to_thread(self.store.update_status, row.id, "no qualifying results", error_message=message)
        logger.info("pack fallback: %s -> %d per-episode request(s)", _request_label(row), len(created))
        for episode_id in created:
            self.enqueue(episode_id)

    async def _watch_downloads(self) -> None:
        while True:
            await asyncio.sleep(config.DOWNLOAD_POLL_INTERVAL_SECONDS)
            try:
                await self._check_downloading()
            except Exception:
                logger.exception("download watch cycle failed")

    async def _refresh_plex(self, media_type: str, target_path) -> None:
        folder = str(target_path.parent) if target_path is not None else None
        try:
            await asyncio.to_thread(plex.refresh_after_import, self.store, media_type, folder)
        except Exception:
            logger.exception("plex refresh after import failed")

    async def _retry_stalled_download(self, row, torrent_hash: str) -> None:
        """Abandon a pick whose swarm never showed up, and search again
        without it.

        Reuses Stage 15's rejection machinery rather than growing a
        second one: the worker's `queued` handler already excludes
        rejected hashes and release names, so blacklisting and
        re-queueing is the whole retry. Both are blacklisted, not just
        the hash — most winners are direct .torrent links that carry no
        hash to compare, so the name is what actually rules the release
        out on the next pass (the same reason api.py's reject route does
        both)."""
        result = dict(row.result or {})
        attempts = int(result.get("stall_attempts") or 1) + 1
        result["stall_attempts"] = attempts

        try:
            await asyncio.to_thread(self.qbt.delete_torrent, torrent_hash, True)
        except QBTError as exc:
            # Worth continuing anyway: the point is to stop re-picking
            # this release, and the blacklist below is what does that.
            logger.warning("request %d: couldn't remove stalled torrent %s: %s", row.id, torrent_hash, exc)

        await asyncio.to_thread(self.store.add_rejected_torrent, row.tmdb_id, torrent_hash)
        winner = result.get("winner") or {}
        if winner.get("fileName"):
            await asyncio.to_thread(self.store.add_rejected_release, row.tmdb_id, winner["fileName"])

        if attempts > config.STALL_MAX_ATTEMPTS:
            message = f"No usable copy found — {config.STALL_MAX_ATTEMPTS} attempts stalled with no seeds."
            logger.info("request %d (%s) downloading -> failed (%s)", row.id, _request_label(row), message)
            await asyncio.to_thread(self.store.update_status, row.id, "failed", message, result)
            return

        logger.info(
            "request %d (%s) downloading -> queued (stalled with no seeds, attempt %d of %d)",
            row.id,
            _request_label(row),
            attempts,
            config.STALL_MAX_ATTEMPTS,
        )
        await asyncio.to_thread(self.store.update_status, row.id, "queued", None, result)

    async def _check_downloading(self) -> None:
        for row in await asyncio.to_thread(self.store.list_requests, "downloading"):
            torrent_hash = (row.result or {}).get("torrent_hash") or await self._adopt_untracked_torrent(row)
            if not torrent_hash:
                continue
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
                found = await asyncio.to_thread(plex.has_in_library, self.store, row.title, row.release_year, row.tmdb_id)
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
            elif _stalled_with_dead_swarm(info):
                await self._retry_stalled_download(row, torrent_hash)
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
            else:
                # Frontend migration Part J2 — the same live fraction this
                # branch's own `>= 1` check above already reads, just no
                # longer discarded once it's less than that. Persisted on
                # every poll so the Requests queue can show a real
                # progress bar instead of an indeterminate "downloading"
                # spinner.
                progress = info.get("progress")
                if progress is not None:
                    await asyncio.to_thread(self.store.update_download_progress, row.id, progress)

    async def _adopt_untracked_torrent(self, row) -> str | None:
        """A "downloading" row whose add never pinned down a hash (two adds
        landing at once from a slow tracker; live 2026-09-17, a Ted season
        pack sat at "downloading" for a day). Adopts the torrent carrying
        the winning release's name that no other request tracks, so the
        watcher can file it; failing that, once the grace period is up,
        marks the row failed so it can be retried."""
        name = ((row.result or {}).get("winner") or {}).get("fileName")
        if name:
            torrents = await asyncio.to_thread(self.qbt.list_torrents)
            claimed = {(r.result or {}).get("torrent_hash") for r in await asyncio.to_thread(self.store.list_requests)}
            for torrent in torrents:
                torrent_hash = (torrent.get("hash") or "").lower()
                if torrent_hash and torrent_hash not in claimed and same_release(torrent.get("name"), name):
                    result = {**(row.result or {}), "torrent_hash": torrent_hash}
                    await asyncio.to_thread(self.store.update_status, row.id, "downloading", None, result)
                    logger.info("request %d (%s) adopted untracked torrent %s", row.id, _request_label(row), torrent_hash)
                    return torrent_hash
        if datetime.now(timezone.utc) - datetime.fromisoformat(row.updated_at) >= _UNTRACKED_GRACE:
            message = "Lost track of this download in qBittorrent"
            logger.warning("request %d (%s) downloading -> failed (%s)", row.id, _request_label(row), message)
            await asyncio.to_thread(self.store.update_status, row.id, "failed", error_message=message)
        return None

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
        except NoVideoFileError:
            message = await self._purge_no_video_torrent(torrent_hash, label)
            logger.warning("request %d (%s) downloading -> cancelled (%s)", row.id, label, message)
            await asyncio.to_thread(self.store.update_status, row.id, "cancelled", error_message=message)
            return
        except (MediaOrganizerError, TMDBError, OSError) as exc:
            logger.warning("request %d (%s) downloading -> downloaded, not filed (%s)", row.id, label, exc)
            await asyncio.to_thread(
                self.store.update_status, row.id, "downloaded, not filed", error_message=str(exc)
            )
            return
        logger.info("request %d (%s) downloading -> complete (organized to %s)", row.id, label, target_path)
        # Stage 12.x's quality-upgrade recheck: the superseded torrent (if
        # any) is folded into the same durable cleanup sweep as the new
        # one, never deleted ahead of it — the new file is safely in place
        # at the same deterministic library path before either one is ever
        # touched, so an upgrade can never leave nothing in the library if
        # the add had failed instead.
        old_hash = (row.result or {}).get("replaces_torrent_hash")
        pending_hashes = [torrent_hash] + ([old_hash] if old_hash else [])
        await asyncio.to_thread(
            self.store.mark_organized, row.id, [str(target_path)], pending_hashes, self._next_cleanup_attempt_at()
        )
        await self._refresh_plex("tv", target_path)

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
        except NoVideoFileError:
            message = await self._purge_no_video_torrent(torrent_hash, label)
            logger.warning("request %d (%s) downloading -> cancelled (%s)", row.id, label, message)
            await asyncio.to_thread(self.store.update_status, row.id, "cancelled", error_message=message)
            return
        except (MediaOrganizerError, TMDBError, OSError) as exc:
            logger.warning("request %d (%s) downloading -> downloaded, not filed (%s)", row.id, label, exc)
            await asyncio.to_thread(
                self.store.update_status, row.id, "downloaded, not filed", error_message=str(exc)
            )
            return
        logger.info("request %d (%s) downloading -> complete (organized to %s)", row.id, label, target_path)

        # frontend migration Part K2: an "overwrite" redownload also
        # deletes the previously organized file once *this* new one is
        # confirmed in place — see mark_organized's own docstring for why
        # this is a direct path, not a torrent hash (the original torrent
        # may have already been cleaned up long ago). Only paths that
        # genuinely differ from this run's own target — organizing to the
        # exact same computed path (same filename) already self-replaces
        # via _link_or_copy's own "target exists -> unlink first" step,
        # nothing extra to schedule in that case.
        superseded_paths: list[str] = []
        if row.redownload_mode == "overwrite":
            # The library ledger knows every copy this app filed for the
            # title, however long ago and whether or not the request row
            # still exists; failing that, the copy Plex points at.
            items = await asyncio.to_thread(self.store.get_library_items, row.tmdb_id, "movie")
            superseded_paths = [it["path"] for it in items if it["path"] != str(target_path) and it["request_id"] != row.id]
            if not superseded_paths:
                previous = await asyncio.to_thread(plex.local_file_for_title, self.store, "movie", row.title, row.release_year, row.tmdb_id)
                if previous is not None and str(previous) != str(target_path):
                    superseded_paths = [str(previous)]

        await asyncio.to_thread(
            self.store.mark_organized,
            row.id,
            [str(target_path)],
            [torrent_hash],
            self._next_cleanup_attempt_at(),
            superseded_paths=superseded_paths,
        )
        await self._refresh_plex("movie", target_path)

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
        except NoVideoFileError:
            message = await self._purge_no_video_torrent(torrent_hash, label)
            logger.warning("pack request %d (%s) downloading -> cancelled (%s)", row.id, label, message)
            await asyncio.to_thread(self.store.update_status, row.id, "cancelled", error_message=message)
            return
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
        await asyncio.to_thread(
            self.store.mark_organized,
            row.id,
            [str(p) for _, _, p in placed],
            [torrent_hash],
            self._next_cleanup_attempt_at(),
        )
        await self._refresh_plex("tv", placed[-1][2] if placed else None)

    async def _purge_no_video_torrent(self, torrent_hash: str, label: str) -> str:
        """A completed torrent with no real video file in it at all is the
        same shape as a fake/malicious release (filler .txt/.jpg plus a
        disguised .exe — confirmed live, 2026-09-14) rather than an
        ordinary organize failure worth leaving on disk for later. Purges
        the torrent and its downloaded files outright instead of leaving
        them sitting in "downloaded, not filed" limbo, and returns the
        message the caller stores on the request row."""
        try:
            await asyncio.to_thread(self.qbt.delete_torrent, torrent_hash, True)
            logger.warning("request (%s): no video file at all — purged as a likely fake release", label)
        except Exception:
            logger.exception("request (%s): no video file at all, but purging the torrent failed", label)
        return "No video file found in torrent (fake/malicious release) — torrent and files removed automatically"

    def _next_cleanup_attempt_at(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=config.SOURCE_CLEANUP_DELAY_SECONDS)).isoformat()

    async def _watch_source_cleanup(self) -> None:
        while True:
            try:
                await self._run_due_source_cleanups()
            except Exception:
                logger.exception("source cleanup watch cycle failed")
            await asyncio.sleep(config.SOURCE_CLEANUP_POLL_INTERVAL_SECONDS)

    async def _run_due_source_cleanups(self) -> None:
        for row in await asyncio.to_thread(self.store.list_due_source_cleanups):
            await self._attempt_source_cleanup(row)

    async def _attempt_source_cleanup(self, row) -> None:
        """Stage 13.x, at the user's explicit request: once a torrent's
        file(s) have been organized into Plex's library layout, remove the
        original torrent(s) — qBittorrent queue entry *and* its own
        downloaded copy — after `config.SOURCE_CLEANUP_DELAY_SECONDS`,
        once a final re-check confirms every organized copy is genuinely
        still there. Durable and restart-safe (a real gap in the original
        Stage 13.x version, found live: a fire-and-forget in-memory
        `asyncio.create_task` that silently lost its work forever if the
        backend restarted, or the one-shot delete itself failed, anywhere
        in the delay window): every attempt re-derives its work fresh from
        `list_due_source_cleanups`, same pattern as every other polling
        loop in this file, so nothing is ever lost to a restart, and a
        failed attempt is retried on the next sweep rather than abandoned.
        Once this marks a row 'done' (success, or a deliberate permanent
        skip below), `list_due_source_cleanups` never surfaces it again —
        no retry after that point, ever.

        Safe by construction, not just by intent: a hardlinked organized
        copy shares the exact same underlying bytes as the
        torrent's own file (deleting one link never touches the data the
        other still points at), and the copy-fallback case already made a
        fully independent copy at organize time — either way, nothing
        unique is ever lost. Deletes via `qbt.delete_torrent()` rather than
        removing files itself, so qBittorrent — which already knows
        exactly which paths belong to this specific torrent — decides what
        "this torrent's files" means, never a path this code guesses at.

        A real, deliberate tradeoff, not a free lunch: this stops the
        torrent seeding earlier than the household's own qBittorrent-side
        seeding-time limit would have. If an organized copy has gone
        missing by the time this runs, that's treated as a permanent,
        deliberate skip (not a transient failure to retry) — the copy
        being gone is itself the anomaly, and retrying forever wouldn't
        fix that.

        Frontend migration Part K2: also deletes `superseded_paths` — the
        previously organized file(s) an "overwrite" redownload replaces —
        under the exact same gate as the torrent cleanup above (only once
        *this* row's own new `organized_paths` are confirmed present), via
        a direct filesystem delete rather than `qbt.delete_torrent()`
        (that prior file's own torrent may already be long gone)."""
        result = row.result or {}
        organized_paths = result.get("organized_paths") or []
        hashes = result.get("pending_cleanup_hashes") or []
        superseded_paths = result.get("superseded_paths") or []
        label = _request_label(row)

        if not hashes and not superseded_paths:
            await asyncio.to_thread(self.store.mark_source_cleanup_done, row.id)
            return

        # An empty organized_paths must never be treated as "nothing to
        # check, proceed" (Python's all([]) is vacuously True) — that
        # would let a row with no on-record organized copy sail straight
        # past the safety check below and delete originals no one ever
        # confirmed were safe to remove.
        missing = [p for p in organized_paths if not await asyncio.to_thread(Path(p).exists)]
        if not organized_paths or missing:
            # Still all-or-nothing on purpose: one torrent produced every
            # one of these files, so while any is missing the original
            # may be the only copy of it left and must not be deleted.
            #
            # What changed is giving up. This used to mark the row done
            # on the first look — permanently, silently — so a path that
            # was merely *momentarily* unreadable (a NAS under load, a
            # file being moved by a Plex scan) orphaned the source folder
            # forever with nothing left to say cleanup was ever owed.
            # That is the shape of the reported failure: folders skipped
            # entirely, more of them the busier the box was. Now it
            # retries a bounded number of times first, and only then
            # gives up — loudly.
            attempts = int(result.get("cleanup_verify_attempts") or 0) + 1
            if attempts < config.SOURCE_CLEANUP_VERIFY_ATTEMPTS:
                logger.info(
                    "source cleanup for request %d (%s) deferred — %d organized copy/copies not readable "
                    "(attempt %d of %d): %s",
                    row.id, label, len(missing), attempts, config.SOURCE_CLEANUP_VERIFY_ATTEMPTS, missing[:3],
                )
                await asyncio.to_thread(
                    self.store.defer_source_cleanup,
                    row.id,
                    hashes,
                    self._next_cleanup_attempt_at(),
                    remaining_superseded_paths=superseded_paths,
                    verify_attempts=attempts,
                )
                return
            logger.warning(
                "source cleanup for request %d (%s) abandoned after %d attempts — organized copy still missing, "
                "leaving the original alone: %s",
                row.id, label, attempts, missing[:3],
            )
            await asyncio.to_thread(self.store.mark_source_cleanup_done, row.id)
            return

        remaining_hashes: list[str] = []
        for torrent_hash in hashes:
            try:
                if await asyncio.to_thread(self.qbt.torrent_info, torrent_hash) is not None:
                    await asyncio.to_thread(self.qbt.delete_torrent, torrent_hash, delete_files=True)
                    logger.info(
                        "source cleanup: removed original torrent %s (%s) after organizing", torrent_hash, label
                    )
                # else: already gone (this app's own doing, or otherwise) — nothing left to clean up for this hash
            except Exception:
                logger.exception("source cleanup failed for torrent %s (%s) — will retry", torrent_hash, label)
                remaining_hashes.append(torrent_hash)

        remaining_superseded: list[str] = []
        for path_str in superseded_paths:
            try:
                path = Path(path_str)
                if await asyncio.to_thread(path.exists):
                    await asyncio.to_thread(path.unlink)
                    await asyncio.to_thread(self.store.remove_library_item, path_str)
                    logger.info(
                        "source cleanup: removed superseded file %r (%s) after redownload overwrite", path_str, label
                    )
                # else: already gone — nothing left to clean up for this path
            except Exception:
                logger.exception("superseded-file cleanup failed for %r (%s) — will retry", path_str, label)
                remaining_superseded.append(path_str)

        if remaining_hashes or remaining_superseded:
            await asyncio.to_thread(
                self.store.defer_source_cleanup,
                row.id,
                remaining_hashes,
                self._next_cleanup_attempt_at(),
                remaining_superseded_paths=remaining_superseded,
            )
        else:
            await asyncio.to_thread(self.store.mark_source_cleanup_done, row.id)

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

    def _season_airstamps(self, identity: ShowIdentity, season_number: int) -> dict[int, datetime]:
        """`{episode_number: released_at_utc}` for one season, from
        TVmaze. `{}` when it doesn't know the show or can't be reached,
        which is the signal to fall back to TMDB's date — see
        tv_resolve.episode_is_released.

        Called per season rather than threaded through every signature:
        the client caches a show's whole episode list for hours, so the
        repeat calls a multi-season backfill makes cost one HTTP request
        between them."""
        whole_show = self.tvmaze.airstamps_for_show(identity.tvdb_id, identity.imdb_id)
        return season_airstamps(whole_show, season_number)

    def _unhandled_episodes_for_season(
        self,
        show: ShowRow,
        identity: ShowIdentity,
        season_number: int,
        episodes: list[dict],
        buffer_hours: float = 0.0,
    ) -> list[int]:
        """Which of this season's already-aired episodes are genuinely
        still unhandled — not already in the `show_episodes` ledger, and
        not already sitting on disk (`find_existing_episode_file`; a match
        there is marked `complete` directly, as a side effect of this call,
        so a first-time subscribe to a show with episodes already on disk
        never re-searches-and-re-adds them). Factored out of
        `_check_show_season` so `check_show()`'s season-range detection
        (Stage 14.x) can reuse the exact same disk/ledger check when
        deciding how far a bundled range extends, without duplicating it.

        `buffer_hours` is `tv_settings.episode_air_buffer_hours`, passed
        straight through to `aired_episode_numbers` — see tv_resolve.py's
        `aired_cutoff_date` for what it does and why."""
        aired = set(
            aired_episode_numbers(
                episodes, buffer_hours=buffer_hours, airstamps=self._season_airstamps(identity, season_number)
            )
        )
        on_plex = self._episodes_on_plex(identity)
        unhandled = []
        for episode_number in sorted(aired):
            if self.store.has_show_episode(show.id, season_number, episode_number):
                continue

            # Plex is the verifier: an episode the server already holds is
            # done, whatever this app's own ledger says about it.
            if (season_number, episode_number) in on_plex:
                self._mark_found(show, identity, season_number, episode_number, "already on Plex, not downloaded by this app")
                continue

            existing_path = find_existing_episode_file(identity, season_number, episode_number)
            if existing_path is not None:
                self._mark_found(
                    show, identity, season_number, episode_number, "found already on disk, not downloaded by this app", str(existing_path)
                )
                continue

            unhandled.append(episode_number)

        return unhandled

    def _episodes_on_plex(self, identity: ShowIdentity) -> set[tuple[int, int]]:
        """What Plex holds of this show, or nothing when Plex isn't linked
        or can't be reached (never a reason to stop a check)."""
        try:
            return plex.plex_show_episodes(self.store, identity.title, identity.first_air_year, identity.tmdb_id) or set()
        except Exception:  # noqa: BLE001 — a Plex hiccup must not break a check
            logger.exception("show check: couldn't list %s on Plex", identity.title)
            return set()

    def _mark_found(self, show: ShowRow, identity: ShowIdentity, season_number: int, episode_number: int, note: str, path: str | None = None) -> None:
        """Records an episode the household already has (on Plex, or on
        disk) as complete, so it's visible in the same history as anything
        else and never requested again."""
        request_row = self.store.create_episode_request(
            tmdb_id=show.tmdb_id,
            show_id=show.id,
            title=identity.title,
            season_number=season_number,
            episode_number=episode_number,
            poster_path=identity.poster_path,
        )
        result = {"note": note}
        if path:
            result["path"] = path
        self.store.update_status(request_row.id, "complete", result=result)
        self.store.add_show_episode(show.id, season_number, episode_number, request_row.id)
        logger.info("show check: show %d (%s) S%02dE%02d %s — not downloading", show.id, show.title, season_number, episode_number, note)

    def _check_show_season(
        self,
        show: ShowRow,
        identity: ShowIdentity,
        season_number: int,
        episodes: list[dict],
        season_complete: bool,
        pack_due: bool,
        buffer_hours: float = 0.0,
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
        - `season_complete=True`, none of it in the ledger yet, and
          `pack_due=True` — a single season-pack request covers the whole
          season instead of one search per episode: an older, fully-aired
          season is realistically far more likely to still have one
          well-seeded pack release than well-seeded individual episodes,
          which tend to go cold once a show has moved on. A season the
          household has been following (some episodes already handled)
          never switches to a pack when it finishes — its last episodes
          come one at a time like the rest.
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
        unhandled = self._unhandled_episodes_for_season(show, identity, season_number, episodes, buffer_hours)
        if not unhandled:
            return 0

        # A pack is for a season the household has none of. Once any of
        # its episodes are in the ledger — a show being followed week to
        # week — the rest arrive one at a time, finale included. Confirmed
        # live 2026-09-17 (Reacher): the season finale dropped and the
        # check queued the whole season pack instead of the one episode.
        aired = set(
            aired_episode_numbers(
                episodes, buffer_hours=buffer_hours, airstamps=self._season_airstamps(identity, season_number)
            )
        )
        untouched = len(unhandled) == len(aired)

        if season_complete and untouched:
            if not pack_due:
                return 0
            # Deliberately not added to show_episodes here — a pack row
            # only earns ledger entries once it's actually organized (see
            # _organize_and_complete_pack), same as a bulk-download
            # request. `_should_attempt_pack()` is what stops a failed
            # attempt from being silently re-tried every cycle.
            request_row = self.store.create_pack_request(
                tmdb_id=show.tmdb_id,
                show_id=show.id,
                title=identity.title,
                season_number=season_number,
                poster_path=identity.poster_path,
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

        # A pack that covers this season (its own, a range, or the whole
        # series) is still on the way: its episodes reach the ledger once
        # it's organized, so asking for them one by one now would fetch
        # them twice.
        if self._pack_in_flight_for(show, season_number):
            return 0

        created = 0
        for episode_number in unhandled:
            request_row = self.store.create_episode_request(
                tmdb_id=show.tmdb_id,
                show_id=show.id,
                title=identity.title,
                season_number=season_number,
                episode_number=episode_number,
                poster_path=identity.poster_path,
            )
            self.store.add_show_episode(show.id, season_number, episode_number, request_row.id)
            self.enqueue(request_row.id)
            created += 1

        return created

    def _pack_in_flight_for(self, show: ShowRow, season_number: int) -> bool:
        for row in self.store.list_requests():
            if row.media_type != "pack" or row.show_id != show.id or row.status not in NON_TERMINAL_STATUSES:
                continue
            if row.season_number is None:
                return True  # whole series
            end = row.season_range_end if row.season_range_end is not None else row.season_number
            if row.season_number <= season_number <= end:
                return True
        return False

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

    def _detect_complete_unhandled_prefix(
        self, show: ShowRow, identity: ShowIdentity, latest_season: int, buffer_hours: float = 0.0
    ) -> int:
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
            if not season_is_complete(
                episodes, buffer_hours=buffer_hours, airstamps=self._season_airstamps(identity, season_number)
            ):
                break
            if not self._unhandled_episodes_for_season(show, identity, season_number, episodes, buffer_hours):
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
        self.store.set_show_tmdb_status(show.id, show_data.get("status"))
        nothing_handled = not self.store.list_show_episodes(show.id)
        # A complete-series pack is for a show the household has none of;
        # a followed show that has just ended gets its last episodes one
        # at a time, same rule as a season below.
        if show_ended and nothing_handled and self._should_attempt_pack(show, None, tv_settings):
            request_row = self.store.create_pack_request(
                tmdb_id=show.tmdb_id,
                show_id=show.id,
                title=identity.title,
                season_number=None,
                poster_path=identity.poster_path,
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
            prefix_end = self._detect_complete_unhandled_prefix(
                show, identity, latest_season, tv_settings.episode_air_buffer_hours
            )
            if prefix_end >= 2 and self._should_attempt_pack(show, 1, tv_settings, season_range_end=prefix_end):
                request_row = self.store.create_pack_request(
                    tmdb_id=show.tmdb_id, show_id=show.id, title=identity.title,
                    season_number=1, season_range_end=prefix_end,
                    poster_path=identity.poster_path,
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
            complete = season_is_complete(
                episodes,
                buffer_hours=tv_settings.episode_air_buffer_hours,
                airstamps=self._season_airstamps(identity, season_number),
            )
            pack_due = complete and self._should_attempt_pack(show, season_number, tv_settings)
            created += self._check_show_season(
                show, identity, season_number, episodes, complete, pack_due, tv_settings.episode_air_buffer_hours
            )

        # A show that has ended or been cancelled, with nothing left to
        # fetch, is done being followed: "Shows you follow" is for series
        # still going. (A download already on the way finishes on its own.)
        if show_ended and created == 0 and show.status == "watching":
            self.store.update_show_status(show.id, "paused")
            logger.info("show check: show %d (%s) has %s with nothing left to fetch — no longer following", show.id, show.title, show_data.get("status", "ended").lower())

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
        Draws on the same search slots the queue consumers do, so
        rechecks and queued requests together stay within
        SEARCH_CONCURRENCY; the add itself is serialised further down by
        pipeline._ADD_LOCK."""
        current = await asyncio.to_thread(self.store.get_request, episode_row.request_id)
        if current is not None and current.status != "complete" and current.status not in self._RECHECK_RETRY_STATUSES:
            return  # actively in progress, or a deliberate cancel — don't burn an attempt on it

        identity = await asyncio.to_thread(resolve_show, show.tmdb_id, self.tmdb)
        pipeline_settings = await asyncio.to_thread(resolve_pipeline_settings, self.store)
        label = f"{show.title} S{episode_row.season_number:02d}E{episode_row.episode_number:02d}"

        async with self._search_slots:
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
            show.poster_path or identity.poster_path,
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
            show.poster_path or identity.poster_path,
        )
        await asyncio.to_thread(self.store.update_status, new_row.id, "downloading", None, summary)
        await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id, new_row.id)
        logger.info(
            "recheck: %s found a better release (composite %s -> %s), replacing once it finishes",
            label,
            current_composite,
            best[1].composite,
        )
