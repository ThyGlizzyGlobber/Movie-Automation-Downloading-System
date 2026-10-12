"""Stage 12.x's episode auto-recheck: retrying an episode that got stuck,
and looking for a better release of one that's already downloaded."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core import config
from app.db import FAILURE_STATUSES, ShowEpisodeRow, ShowRow
from app.downloads.pipeline import download_episode, find_best_episode_candidate
from app.downloads.summaries import result_summary
from app.downloads.tv_settings import TVScheduleSettings, resolve_tv_settings
from app.matching.pipeline_settings import resolve_pipeline_settings
from app.matching.tv_resolve import resolve_show

logger = logging.getLogger(__name__)


class Rechecks:
    """Mixed into Worker (worker.py), whose state these methods share."""

    # -- Stage 12.x: episode auto-recheck (retry a stuck episode, or look
    #    for a better release once one's already downloaded). --

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
        # Claimed while the setting was off, so it is not this loop's to
        # touch — not now, and not if the setting is turned on later.
        # Without this, enabling the setting made every episode ever
        # downloaded due at once: nothing is ever rechecked while it is
        # off, so `last_rechecked_at` stays null for all of them and the
        # interval below is measured from a `created_at` long past.
        # Turning it on is a statement about what to do next, not a
        # licence to go back over a library nobody asked to be redone.
        if not episode_row.recheck_opted_in:
            return False
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
        if current is not None and current.status != "complete" and current.status not in FAILURE_STATUSES:
            return  # actively in progress, or a deliberate cancel — don't burn an attempt on it

        identity = await asyncio.to_thread(resolve_show, show.tmdb_id, self.tmdb)
        pipeline_settings = await asyncio.to_thread(resolve_pipeline_settings, self.store)
        label = f"{show.title} S{episode_row.season_number:02d}E{episode_row.episode_number:02d}"

        async with self._search_slots:
            if current is None or current.status != "complete":
                await self._recheck_retry(show, episode_row, identity, pipeline_settings, label)
            else:
                await self._recheck_upgrade(show, episode_row, current, identity, pipeline_settings, label)

    def _rejected_copies(self, tmdb_id: int) -> tuple[set[str], list[dict]]:
        return self.store.get_rejected_torrent_hashes(tmdb_id), self.store.get_rejected_releases(tmdb_id)

    async def _recheck_retry(self, show, episode_row, identity, pipeline_settings, label: str) -> None:
        rejected_hashes, rejected_releases = await asyncio.to_thread(self._rejected_copies, show.tmdb_id)
        result = await asyncio.to_thread(
            download_episode,
            identity,
            episode_row.season_number,
            episode_row.episode_number,
            self.qbt,
            pipeline_settings,
            rejected_hashes,
            rejected_releases,
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
            asks_from=episode_row.request_id,
        )
        await asyncio.to_thread(self.store.update_status, new_row.id, "downloading", None, result_summary(result))
        await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id, new_row.id)
        logger.info("recheck: %s found a release on retry (attempt %d)", label, episode_row.recheck_count + 1)

    async def _recheck_upgrade(self, show, episode_row, current, identity, pipeline_settings, label: str) -> None:
        current_composite = ((current.result or {}).get("score") or {}).get("composite")
        rejected_hashes, rejected_releases = await asyncio.to_thread(self._rejected_copies, show.tmdb_id)
        best = await asyncio.to_thread(
            find_best_episode_candidate,
            identity,
            episode_row.season_number,
            episode_row.episode_number,
            self.qbt,
            pipeline_settings,
            rejected_hashes,
            rejected_releases,
        )
        if best is None or (current_composite is not None and best[1].composite <= current_composite):
            await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id)
            return

        old_hash = (current.result or {}).get("torrent_hash")
        result = await asyncio.to_thread(
            download_episode,
            identity,
            episode_row.season_number,
            episode_row.episode_number,
            self.qbt,
            pipeline_settings,
            rejected_hashes,
            rejected_releases,
        )
        if result.status != "added":
            # The peek found something better a moment ago, but it didn't
            # actually land (e.g. qBittorrent rejected the add) — leave the
            # existing complete episode exactly as it is.
            await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id)
            return

        summary = result_summary(result)
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
            asks_from=episode_row.request_id,
        )
        await asyncio.to_thread(self.store.update_status, new_row.id, "downloading", None, summary)
        await asyncio.to_thread(self.store.record_episode_recheck, episode_row.id, new_row.id)
        logger.info(
            "recheck: %s found a better release (composite %s -> %s), replacing once it finishes",
            label,
            current_composite,
            best[1].composite,
        )
