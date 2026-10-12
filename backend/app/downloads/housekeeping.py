"""The worker's tidying loops: removing source torrents once their files
are safely filed, download folders nothing owns any more, request history
past the retention policy, and keeping a box's Plex libraries listing every
drive."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.box import drives, plex as box_plex
from app.core import config
from app.downloads.summaries import request_label
from app.library import reconcile

logger = logging.getLogger(__name__)


# How often a box checks Plex's libraries still list every drive.
BOX_LIBRARIES_INTERVAL_SECONDS = 60


class Housekeeping:
    """Mixed into Worker (worker.py), whose state these methods share."""

    def _next_cleanup_attempt_at(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=config.SOURCE_CLEANUP_DELAY_SECONDS)).isoformat()

    async def _watch_orphaned_downloads(self) -> None:
        """The backstop for every download folder the per-request cleanup
        above cannot reach.

        That cleanup only ever deletes *through* qBittorrent, so its
        "torrent already gone" branch has nothing to act on and marks the
        row done — permanently, since list_due_source_cleanups never
        surfaces it again. The folder then sits in the library root for
        good. That branch is not an edge case here: a share-limit rule
        that drops a torrent minutes after it finishes seeding will
        routinely beat the cleanup to it, and a pack re-filed by
        `retry-parked-packs` has no torrent left by definition — so the
        disk fallback that makes those recoverable would otherwise strand
        a folder every single time it worked.

        reconcile.remove_orphaned_download_dirs already solves this and
        was already correct; it was simply only reachable by hand, via
        `app.cli cleanup-orphans`. Automating what the app was supposed
        to do on its own is the whole point of the feature.

        Safe because of what it matches on, not how carefully it is
        scheduled: organize places a library copy with os.link, so a
        filed file and its download are the same inode, and a folder
        qualifies only when it holds at least one video and *every*
        video in it is a redundant link of something in the library. One
        unrecognised video and the folder stays whole. No name matching,
        no size guessing — see reconcile._filed_index."""
        while True:
            await asyncio.sleep(config.ORPHAN_SWEEP_INTERVAL_SECONDS)
            try:
                await self._sweep_orphaned_downloads()
            except Exception:
                logger.exception("orphan sweep cycle failed")

    async def _watch_box_libraries(self) -> None:
        """On a box: keeps Plex's Movies and TV Shows listing every drive,
        so a drive added from Settings › Storage shows up in Plex by itself
        (app/box/plex.py). Only does anything when the drives have changed
        since the last time Plex was told."""
        while True:
            await asyncio.sleep(BOX_LIBRARIES_INTERVAL_SECONDS)
            try:
                await self._sync_box_libraries()
            except Exception:
                logger.exception("couldn't bring Plex's libraries up to date with the drives")

    async def _sync_box_libraries(self) -> list[str]:
        server = await asyncio.to_thread(self.store.primary_plex_server)
        if server is None:
            return []
        folders = await asyncio.to_thread(box_plex.wanted_folders)
        settings = await asyncio.to_thread(self.store.get_settings)
        drop = box_plex.dropped_folders(settings)
        signature = json.dumps({**folders, "drop": drop}, sort_keys=True)
        if settings.get("box_plex_folders") == signature:
            return []
        changes = await asyncio.to_thread(box_plex.sync_libraries, server.url, server.token, folders, None, drop)
        await asyncio.to_thread(self.store.update_settings, {"box_plex_folders": signature})
        if changes:
            logger.info("Plex libraries on the box: %s", "; ".join(changes))
        return changes

    async def _sweep_orphaned_downloads(self) -> list[dict]:
        roots = [str(root) for root in drives.download_roots()]
        swept = await asyncio.to_thread(reconcile.remove_orphaned_download_dirs, self.store, roots, True)
        # Then the folders whose files were rewritten rather than linked
        # on the way in, which share no inode to match on. Separately
        # guarded: it needs qBittorrent, and qBittorrent being down must
        # not stop the half above.
        try:
            swept += await asyncio.to_thread(
                reconcile.remove_stranded_release_dirs, self.store, self.qbt, roots, True
            )
        except Exception:
            logger.exception("orphan sweep: couldn't check for stranded release folders")
        removed = [item for item in swept if item.get("removed")]
        if removed:
            logger.info(
                "orphan sweep: removed %d already-filed download folder(s), %.1f GB",
                len(removed),
                sum(item["size_bytes"] or 0 for item in removed) / 1_000_000_000,
            )
        return swept

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
        label = request_label(row)

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
                # else: the torrent is already gone (this app's own doing, a
                # share-limit rule, or a manual removal), so there is nothing
                # left here to delete *through*. Its files may well still be on
                # disk; _watch_orphaned_downloads is what collects those — by
                # hardlink identity, or by the release name the ledger keeps
                # for a file that was rewritten rather than linked.
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
