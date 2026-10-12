"""Stage 12's standing show subscriptions: checking each followed show for
episodes that have aired and aren't on Plex or on their way, and asking for
them — a whole season as one pack where that's the better bet."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.catalogue.tmdb import TMDBError
from app.catalogue.tvmaze import season_airstamps
from app.db import NON_TERMINAL_STATUSES, ShowRow
from app.downloads.tv_settings import TVScheduleSettings, resolve_tv_settings
from app.library.filing import find_existing_episode_file
from app.matching.tv_resolve import (
    ShowIdentity,
    aired_episode_numbers,
    identity_from_show,
    season_is_complete,
)
from app.plex import library as plex_library

logger = logging.getLogger(__name__)


class ShowSchedule:
    """Mixed into Worker (worker.py), whose state these methods share."""

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
            return plex_library.show_episodes_on_any_server(self.store, identity.title, identity.first_air_year, identity.tmdb_id) or set()
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
        for row in self.store.list_live_packs_for_show(show.id):
            if row.season_number is None:
                return True  # whole series
            end = row.season_range_end if row.season_range_end is not None else row.season_number
            if row.season_number <= season_number <= end:
                return True
        return False

    # "split" counts as done: a pack that handed its work to per-season
    # or per-episode requests must not be re-attempted, or every recheck
    # cycle re-runs the same fallback and adds another copy of itself.
    _PACK_DONE_STATUSES = {"complete", "split"}

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
        per `tv_settings.TVScheduleSettings`) and api/shows.py's `POST /api/shows`
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
            show_data = self.tmdb.get_tv(show.tmdb_id)
            identity = identity_from_show(show.tmdb_id, show_data)
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
