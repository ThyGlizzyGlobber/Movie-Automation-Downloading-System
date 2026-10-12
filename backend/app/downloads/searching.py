"""Taking queued requests one at a time through the pipeline — search,
filter, score, add — and, when a whole season or series can't be found as
one pack, falling back to its seasons or its episodes. How many run at once
is bounded by the worker's search slots (config.SEARCH_CONCURRENCY), which
the recheck loop draws on too."""

import asyncio
import dataclasses
import logging

from app.box import drives
from app.catalogue.tmdb import TMDBError
from app.db import NON_TERMINAL_STATUSES
from app.downloads.pipeline import download, download_episode, download_pack
from app.downloads.summaries import request_label, result_summary, score_summary
from app.downloads.tv_settings import resolve_tv_settings
from app.matching.pipeline_settings import resolve_pipeline_settings
from app.matching.tv_resolve import ShowIdentity, aired_episode_numbers, resolve_show

logger = logging.getLogger(__name__)


# How often a box with no drive yet looks again before taking a request.
NO_DRIVE_RECHECK_SECONDS = 30

# Pipeline statuses that map directly onto a terminal request status of the
# same name; anything else falls through to "failed" (see _run_one).
_DIRECT_TERMINAL_STATUSES = {"no qualifying results", "insufficient free space"}


class Searching:
    """Mixed into Worker (worker.py), whose state these methods share."""

    async def _process_queue(self) -> None:
        """One consumer. `start()` runs SEARCH_CONCURRENCY of these, so
        several requests are searched at once; the semaphore is what
        actually bounds it, since the recheck loop draws on the same
        slots.

        On a box with no drive yet there's nowhere to download to, so a
        request waits, still queued, and goes ahead once a drive is added
        (app/box/drives.py)."""
        while True:
            request_id = await self.queue.get()
            while not await asyncio.to_thread(drives.ready):
                await asyncio.sleep(NO_DRIVE_RECHECK_SECONDS)
            self._pending.discard(request_id)
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
            # Stage 15: copies explicitly rejected as genuinely defective (or
            # abandoned as stalled) on a prior attempt for this same
            # movie/show — excluded from this fresh search so it never
            # re-selects the exact same bad release (see api/requests.py's POST
            # /api/requests/{id}/reject).
            rejected_hashes, rejected_releases = await asyncio.to_thread(self._rejected_copies, row.tmdb_id)
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
                    rejected_releases,
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
                    rejected_releases,
                )
            else:
                result = await asyncio.to_thread(
                    download, row.tmdb_id, self.tmdb, self.qbt, settings, rejected_hashes, rejected_releases
                )
        except Exception as exc:  # fail safe, not silent — never leave a row stuck
            logger.exception("request %d failed", request_id)
            await asyncio.to_thread(self.store.update_status, request_id, "failed", error_message=str(exc))
            return

        summary = result_summary(result)
        logger.info(
            "request %d (%s) pipeline result=%s variant=%r candidates=%d %s",
            request_id,
            request_label(row),
            result.status,
            result.variant_used,
            result.candidates_considered,
            score_summary(result.score),
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
                logger.exception("pack fallback: couldn't fetch TMDB data for %s", request_label(row))
                return
            seasons = list(range(1, (show_data.get("number_of_seasons") or 0) + 1))

        tv_settings = await asyncio.to_thread(resolve_tv_settings, self.store)
        on_plex = await asyncio.to_thread(self._episodes_on_plex, identity)
        created: list[int] = []
        covered = 0
        for season_number in seasons:
            try:
                episodes = await asyncio.to_thread(self.tmdb.get_tv_season, row.tmdb_id, season_number)
            except TMDBError:
                logger.exception("pack fallback: couldn't fetch season %d for %s", season_number, request_label(row))
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
                covered += 1
                continue
            attempts = await asyncio.to_thread(self.store.list_pack_requests_for_show, show.id, season_number, None)
            if attempts and (attempts[0].status in NON_TERMINAL_STATUSES or attempts[0].status in self._PACK_DONE_STATUSES):
                covered += 1
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
                asks_from=row.id,
            )
            created.append(season_row.id)

        scope = "series pack" if row.season_number is None else "season-range pack"
        lead = f"{note}, so" if note else f"No {scope} found, so"
        if created:
            n = len(created)
            message = f"{lead} its {n} season{'s were' if n != 1 else ' was'} requested one at a time."
        elif covered:
            message = f"{lead} nothing was added: every season is already requested or on Plex."
        else:
            message = f"{lead} there were no aired seasons to ask for instead."
        # "split", not "no qualifying results". This request did its job —
        # its seasons own the work now — and recording it as a failure
        # did real damage: the Requests page filed it under Failed, its
        # "No match" outranked every finished episode on the show's card
        # (The Pitt read "No match" with 46 of 48 on Plex), and the retry
        # gate treated it as a failed attempt and ran it again, which is
        # where that show's second "Whole series" row came from.
        #
        # A fallback with nothing to hand off at all — nothing aired yet,
        # or TMDB had nothing — is still "no qualifying results". That one
        # really is a no-match, and calling it "split" would say something
        # was split up when nothing was.
        status = "split" if created or covered else "no qualifying results"
        await asyncio.to_thread(self.store.update_status, row.id, status, error_message=message)
        logger.info("pack fallback: %s -> %d per-season request(s)", request_label(row), len(created))
        for season_id in created:
            self.enqueue(season_id)

    async def _fall_back_to_episodes(self, row, identity: ShowIdentity) -> None:
        """A season request that found no pack is re-issued as one request
        per aired episode. Plenty of older or smaller shows only ever
        circulate as single episodes. Episodes already in the ledger
        (handled by an earlier request, or found on disk) are left alone;
        the new rows inherit the request's resolution floor, and
        the pack row is marked "split" and says what happened next."""
        show = await asyncio.to_thread(self.store.get_show, row.show_id)
        if show is None:
            return
        seasons = [row.season_number]

        tv_settings = await asyncio.to_thread(resolve_tv_settings, self.store)
        on_plex = await asyncio.to_thread(self._episodes_on_plex, identity)
        created: list[int] = []
        covered = 0
        for season_number in seasons:
            try:
                episodes = await asyncio.to_thread(self.tmdb.get_tv_season, row.tmdb_id, season_number)
            except TMDBError:
                logger.exception("pack fallback: couldn't fetch season %d for %s", season_number, request_label(row))
                continue
            stamps = await asyncio.to_thread(self._season_airstamps, identity, season_number)
            for episode_number in aired_episode_numbers(
                episodes, buffer_hours=tv_settings.episode_air_buffer_hours, airstamps=stamps
            ):
                if await asyncio.to_thread(self.store.has_live_show_episode, show.id, season_number, episode_number):
                    covered += 1
                    continue
                if (season_number, episode_number) in on_plex:
                    await asyncio.to_thread(self._mark_found, show, identity, season_number, episode_number, "already on Plex, not downloaded by this app")
                    covered += 1
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
                    asks_from=row.id,
                )
                await asyncio.to_thread(self.store.add_show_episode, show.id, season_number, episode_number, episode_row.id)
                created.append(episode_row.id)

        if created:
            n = len(created)
            message = f"No season pack found, so its {n} episode{'s were' if n != 1 else ' was'} requested one by one."
        elif covered:
            message = "No season pack found, and every aired episode is already requested."
        else:
            message = "No season pack found, and no aired episodes to ask for instead."
        # See _fall_back_to_seasons for why a handoff is not a failure —
        # and why having nothing to hand off still is.
        status = "split" if created or covered else "no qualifying results"
        await asyncio.to_thread(self.store.update_status, row.id, status, error_message=message)
        logger.info("pack fallback: %s -> %d per-episode request(s)", request_label(row), len(created))
        for episode_id in created:
            self.enqueue(episode_id)
