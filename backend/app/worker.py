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
"""

import asyncio
import logging

from app import config, plex
from app.db import RequestStore
from app.pipeline import download
from app.qbt import QBTClient
from app.tmdb import TMDBClient

logger = logging.getLogger("app.worker")

# Pipeline statuses that map directly onto a terminal request status of the
# same name; anything else falls through to "failed" (see _run_one).
_DIRECT_TERMINAL_STATUSES = {"no qualifying results", "insufficient free space"}


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
            result = await asyncio.to_thread(download, row.tmdb_id, self.tmdb, self.qbt)
        except Exception as exc:  # fail safe, not silent — never leave a row stuck
            logger.exception("request %d failed", request_id)
            await asyncio.to_thread(self.store.update_status, request_id, "failed", error_message=str(exc))
            return

        summary = _result_summary(result)
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
                # Gone from qBittorrent without this app deleting it itself
                # (api.py's cancel route sets "cancelled" directly and never
                # reaches this branch). Two real causes look identical here:
                # someone deleted it, or qBittorrent's own "remove torrent
                # after completion" setting just cleaned up a *finished*
                # download. Ask Plex before assuming the worse one.
                found = await asyncio.to_thread(plex.has_in_library, self.store, row.title, row.release_year)
                if found:
                    await asyncio.to_thread(self.store.update_status, row.id, "complete")
                else:
                    message = (
                        "Removed from qBittorrent outside this app"
                        if found is None
                        else "Removed from qBittorrent outside this app, and not found in Plex"
                    )
                    await asyncio.to_thread(self.store.update_status, row.id, "cancelled", error_message=message)
            elif info.get("progress", 0) >= 1:
                await asyncio.to_thread(self.store.update_status, row.id, "complete")

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
