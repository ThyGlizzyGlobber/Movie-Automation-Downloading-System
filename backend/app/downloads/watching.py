"""Watching what qBittorrent is downloading: progress, a torrent that
stalled with nobody seeding it, one that vanished, and filing each finished
download into the library, then refreshing Plex and nudging its audio.

A torrent disappearing from qBittorrent isn't always a deletion: with
"remove torrent after completion" on, a finished download vanishes the same
way a cancelled one would. Before concluding "cancelled",
`_check_downloading` asks Plex whether the title made it into the library —
"fail safe, not best guess"."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from app.catalogue.tmdb import TMDBError
from app.core import config
from app.downloads.pipeline import same_release
from app.downloads.summaries import request_label
from app.library.filing import (
    MediaOrganizerError,
    NoVideoFileError,
    organize_episode,
    organize_movie,
    select_video_file,
)
from app.library.packs import organize_pack, pack_season_span
from app.matching.pipeline_settings import resolve_pipeline_settings
from app.matching.resolve import resolve
from app.matching.tv_resolve import (
    episode_placement_lookup,
    episode_title_lookup,
    find_predecessor_show,
    pack_alignment_lookup,
    resolve_show,
)
from app.plex import audio as plex_audio, client as plex_client, library as plex_library
from app.qbittorrent.client import QBTError

logger = logging.getLogger(__name__)


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


class DownloadWatching:
    """Mixed into Worker (worker.py), whose state these methods share."""

    async def _watch_downloads(self) -> None:
        while True:
            await asyncio.sleep(config.DOWNLOAD_POLL_INTERVAL_SECONDS)
            try:
                await self._check_downloading()
            except Exception:
                logger.exception("download watch cycle failed")
            try:
                await self._requeue_orphans()
            except Exception:
                logger.exception("orphan requeue sweep failed")

    async def _refresh_plex(
        self,
        media_type: str,
        target_path,
        title: str | None = None,
        year: int | None = None,
        tmdb_id: int | None = None,
    ) -> None:
        folder = str(target_path.parent) if target_path is not None else None
        # Every server, not just the primary: they all read the same
        # library mounts, so a file filed for one is new to each of them,
        # and a server left to find it on its own schedule shows it hours
        # late. Asked together, since one server being slow or down is no
        # reason to hold up the others.
        servers = await asyncio.to_thread(self.store.list_plex_servers)

        async def _refresh(server) -> None:
            try:
                await asyncio.to_thread(plex_library.refresh_after_import, self.store, server, media_type, folder)
            except Exception:
                logger.exception("plex refresh after import failed on %s", server.display_name)

        await asyncio.gather(*(_refresh(server) for server in servers))
        if not title:
            return
        # And then tell Plex which track to use, rather than trusting it
        # to have read the one the file asks for. Setting the flag in the
        # file decides Plex's *first* choice and nothing after it, and
        # that turned out to be a weaker guarantee than it sounds: a
        # viewer's own language preference sits above it, a failed remux
        # leaves the old flag in place, and Plex records whatever it
        # picked and never revisits it. Seen on The Gorge, whose file had
        # English as its only default while Plex played the Russian dub.
        # Not awaited: Plex has to finish scanning before the part
        # exists, so this waits and retries, and a download completing
        # must not sit behind that. Failures are its own to log. Once per
        # server, each on its own: every server keeps its own idea of
        # which track a part plays, and scans at its own pace.
        async def _nudge(server) -> None:
            try:
                settings = await asyncio.to_thread(resolve_pipeline_settings, self.store)
                raw = await asyncio.to_thread(self.store.get_settings)
                client = plex_client.client_from_settings(raw)
                await asyncio.to_thread(
                    plex_audio.select_audio_after_import,
                    self.store, server, client, media_type, title, year, tmdb_id,
                    settings.preferred_audio_language,
                )
            except Exception:
                logger.exception("plex audio selection after import failed on %s", server.display_name)

        for server in servers:
            self._audio_nudges.add(task := asyncio.create_task(_nudge(server)))
            task.add_done_callback(self._audio_nudges.discard)

    async def _retry_stalled_download(self, row, torrent_hash: str) -> None:
        """Abandon a pick whose swarm never showed up, and search again
        without it.

        Reuses Stage 15's rejection machinery rather than growing a
        second one: the worker's `queued` handler already excludes
        rejected hashes and release names, so blacklisting and
        re-queueing is the whole retry. Both are blacklisted, not just
        the hash — most winners are direct .torrent links that carry no
        hash to compare, so the name is what actually rules the release
        out on the next pass (the same reason api/requests.py's reject route does
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
            logger.info("request %d (%s) downloading -> failed (%s)", row.id, request_label(row), message)
            await asyncio.to_thread(self.store.update_status, row.id, "failed", message, result)
            return

        logger.info(
            "request %d (%s) downloading -> queued (stalled with no seeds, attempt %d of %d)",
            row.id,
            request_label(row),
            attempts,
            config.STALL_MAX_ATTEMPTS,
        )
        await asyncio.to_thread(self.store.update_status, row.id, "queued", None, result)
        # The database saying "queued" is not the same as being queued.
        # Without this the row sat on "Waiting its turn" with no turn
        # coming — nothing in memory knew about it — until the next
        # process restart, when boot recovery re-enqueued every queued
        # row. That is why it looked intermittent: a git pull reloads
        # uvicorn and quietly fixes it, and the next stalled download
        # breaks it again. Hanging Up sat like that for three days.
        self.enqueue(row.id)

    async def _check_downloading(self) -> None:
        tracked = []
        for row in await asyncio.to_thread(self.store.list_requests, "downloading"):
            torrent_hash = (row.result or {}).get("torrent_hash") or await self._adopt_untracked_torrent(row)
            if torrent_hash:
                tracked.append((row, torrent_hash))
        # One qBittorrent round trip for the whole poll, not one per row.
        infos = await asyncio.to_thread(self.qbt.torrents_info_many, [h for _, h in tracked]) if tracked else {}
        for row, torrent_hash in tracked:
            info = infos.get(torrent_hash.lower())
            if info is None:
                if row.media_type in ("episode", "pack"):
                    # plex_library.has_in_any_library only supports a movie lookup
                    # (PlexClient.has_movie is type=1 only) — a TV
                    # equivalent is a real, documented gap, same style as
                    # this project's other named-not-solved gaps, so an
                    # episode's (or a pack's) disappearance is reported as
                    # an unconfirmed removal rather than guessing at a
                    # check that doesn't fit the data.
                    message = "Removed from qBittorrent outside this app"
                    logger.info("request %d (%s) downloading -> cancelled (%s)", row.id, request_label(row), message)
                    await asyncio.to_thread(self.store.update_status, row.id, "cancelled", error_message=message)
                    continue
                # Gone from qBittorrent without this app deleting it itself
                # (api/requests.py's cancel route sets "cancelled" directly and never
                # reaches this branch). Two real causes look identical here:
                # someone deleted it, or qBittorrent's own "remove torrent
                # after completion" setting just cleaned up a *finished*
                # download. Ask Plex before assuming the worse one.
                found = await asyncio.to_thread(plex_library.has_in_any_library, self.store, row.title, row.release_year, row.tmdb_id)
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
                # longer discarded once it's less than that. Persisted
                # whenever it moves so the Requests queue can show a real
                # progress bar instead of an indeterminate "downloading"
                # spinner — a paused or stalled torrent costs no write.
                progress = info.get("progress")
                if progress is not None and progress != row.download_progress:
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
                    logger.info("request %d (%s) adopted untracked torrent %s", row.id, request_label(row), torrent_hash)
                    return torrent_hash
        if datetime.now(timezone.utc) - datetime.fromisoformat(row.updated_at) >= _UNTRACKED_GRACE:
            message = "Lost track of this download in qBittorrent"
            logger.warning("request %d (%s) downloading -> failed (%s)", row.id, request_label(row), message)
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
        label = request_label(row)
        try:
            identity = await asyncio.to_thread(resolve_show, row.tmdb_id, self.tmdb)
            source_path = await asyncio.to_thread(
                select_video_file, self.qbt, torrent_hash, config.QBIT_TV_SAVE_PATH, config.TV_LIBRARY_ROOT
            )
            title = await asyncio.to_thread(
                episode_title_lookup(row.tmdb_id, self.tmdb), row.season_number, row.episode_number
            )
            audio = (await asyncio.to_thread(resolve_pipeline_settings, self.store)).preferred_audio_language
            target_path = await asyncio.to_thread(
                organize_episode, identity, row.season_number, row.episode_number, source_path, title, audio
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
        await self._refresh_plex("tv", target_path, identity.title, identity.first_air_year, row.tmdb_id)

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
        label = request_label(row)
        try:
            identity = await asyncio.to_thread(resolve, row.tmdb_id, self.tmdb)
            source_path = await asyncio.to_thread(
                select_video_file, self.qbt, torrent_hash, config.QBIT_MOVIE_SAVE_PATH, config.MOVIE_LIBRARY_ROOT
            )
            audio = (await asyncio.to_thread(resolve_pipeline_settings, self.store)).preferred_audio_language
            target_path = await asyncio.to_thread(organize_movie, identity, source_path, audio)
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
                previous = await asyncio.to_thread(
                    plex_library.local_file_on_any_server, self.store, "movie", row.title, row.release_year, row.tmdb_id
                )
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
        await self._refresh_plex("movie", target_path, identity.title, identity.release_year, row.tmdb_id)

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
        # The name the indexer gave the release, which is how organize_pack
        # finds the download on disk if qBittorrent has already dropped the
        # torrent (a share-limit rule can remove it within minutes of the
        # download finishing, data left in place).
        release_name = ((row.result or {}).get("winner") or {}).get("fileName")
        label = request_label(row)
        try:
            identity = await asyncio.to_thread(resolve_show, row.tmdb_id, self.tmdb)
            # One lookup for the whole pack: it caches per season, so a
            # season pack costs one TMDB call and a complete-series pack
            # one per season it turns out to contain — none for a season
            # it doesn't. It also decides where a file goes, not just
            # what it is called; see episode_placement_lookup.
            place = episode_placement_lookup(row.tmdb_id, self.tmdb, identity.title)
            align = pack_alignment_lookup(row.tmdb_id, self.tmdb)
            audio = (await asyncio.to_thread(resolve_pipeline_settings, self.store)).preferred_audio_language

            # A "complete series" pack of a continuation show is often
            # the whole franchise in airing order, numbered straight
            # through: Justice League Unlimited has three seasons and
            # every pack of it is S01-S05, because it carries Justice
            # League's two first. Filed as one show those become seasons
            # four and five of a three-season show, which Plex shelves
            # as specials, while the two that really are the other
            # series get filed under this one. See
            # tv_resolve.find_predecessor_show.
            span = await asyncio.to_thread(pack_season_span, torrent_hash, self.qbt, release_name)
            spare, predecessor = 0, None
            # Numbered past this show's last season, but not from season 1:
            # the numbering belongs to something bigger than this show and
            # there's no telling what — refused rather than filed as seasons
            # the show doesn't have.
            if span and identity.number_of_seasons and span[0] > 1 and span[1] > identity.number_of_seasons:
                raise MediaOrganizerError(
                    f"this pack holds seasons {span[0]}-{span[1]} but {identity.title} has "
                    f"{identity.number_of_seasons} — its numbering belongs to something bigger than this "
                    f"show, and filing it would invent seasons this show does not have"
                )
            if span and identity.number_of_seasons and span[0] == 1:
                spare = span[1] - identity.number_of_seasons
                if spare > 0:
                    predecessor = await asyncio.to_thread(
                        find_predecessor_show, identity, self.tmdb, spare
                    )
                    if predecessor is None:
                        raise MediaOrganizerError(
                            f"this pack holds seasons 1-{span[1]} but {identity.title} has "
                            f"{identity.number_of_seasons}, and nothing on TMDB accounts for the extra "
                            f"{spare} — filing it would invent seasons this show does not have"
                        )

            if predecessor is not None:
                # Two passes, each claiming its own slice of the pack and
                # renumbering it onto that show's own seasons.
                lead_place = episode_placement_lookup(predecessor.tmdb_id, self.tmdb, predecessor.title)
                lead_align = pack_alignment_lookup(predecessor.tmdb_id, self.tmdb)
                lead = await asyncio.to_thread(
                    organize_pack, predecessor, torrent_hash, self.qbt, release_name,
                    lead_place, audio, (1, spare), 0, lead_align,
                )
                logger.info(
                    "pack request %d: filed %d file(s) as %r, which this pack carried in front of %r",
                    row.id, len(lead), predecessor.title, identity.title,
                )
                placed = await asyncio.to_thread(
                    organize_pack, identity, torrent_hash, self.qbt, release_name,
                    place, audio, (spare + 1, span[1]), -spare, align,
                )
            else:
                placed = await asyncio.to_thread(
                    organize_pack, identity, torrent_hash, self.qbt, release_name, place, audio, None, 0, align
                )
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
                self.store.create_episode_request, row.tmdb_id, row.show_id, identity.title, season, episode,
                asks_from=row.id,
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
        await self._refresh_plex(
            "tv", placed[-1][2] if placed else None, identity.title, identity.first_air_year, row.tmdb_id
        )

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
