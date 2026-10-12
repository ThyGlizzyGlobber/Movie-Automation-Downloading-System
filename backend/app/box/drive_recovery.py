"""A drive that has failed, on a box: what was on it, and downloading it
all again onto the drives that are left (app/box/drives.py).

What was on it is the library ledger's word (`library_items`): every file
this app filed there. Anything a person copied on by hand has no record,
so it can't be listed or fetched again — said on the page, not guessed at.

Asking for them again is ordinary requests, the same ones a person would
make: a movie its own request; a season that came as a season pack and
was all on the drive a season pack again, which falls back to single
episodes by itself when there isn't one; and any other episodes one at a
time — the ones that survived on another drive aren't fetched twice, and
a season someone only ever had an episode of isn't fetched whole. Their ledger
entries go first — the files are gone, and the ledger saying otherwise
is what would stop the new copies being filed — and Plex stops listing
the drive's folders, or it would answer "already on Plex" for them all.
"""

import logging
import re
from collections import defaultdict
from pathlib import Path

from app.box import drives, plex as box_plex
from app.catalogue.tmdb import TMDBClient, TMDBError
from app.db import RequestStore
from app.matching.resolve import resolve
from app.matching.tv_resolve import resolve_show

logger = logging.getLogger(__name__)

DRIVE_ID = re.compile(r"^drive[1-9][0-9]{0,2}$")
_TMDB_HINT = re.compile(r"\s*\{tmdb-(\d+)\}\s*$")
_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")
_EPISODE = re.compile(r"\bs(\d{1,3})e(\d{1,4})\b", re.IGNORECASE)


def drive_folder(drive_id: str) -> str:
    return f"{drives.MEDIA_DRIVES_ROOT.rstrip('/')}/{drive_id}"


def library_folders(drive_id: str) -> list[str]:
    top = drive_folder(drive_id)
    return [f"{top}/{drives.MOVIES}", f"{top}/{drives.TV_SHOWS}"]


def _title_folder(name: str) -> tuple[str, int | None, int | None]:
    """"Title (2019) {tmdb-123}" as this app names a title's folder
    (library/filing.py's build_movie_path / build_episode_path)."""
    hint = _TMDB_HINT.search(name)
    name = _TMDB_HINT.sub("", name)
    year = _YEAR.search(name)
    return _YEAR.sub("", name).strip(), int(year.group(1)) if year else None, int(hint.group(1)) if hint else None


def _parse(item: dict, top: str) -> dict | None:
    """One ledger row as a title: which movie, or which show's episode.
    The folder names the title, and for a show a pack carried in front of
    another (worker.py's lead files) it names the right one."""
    parts = Path(item["path"]).relative_to(top).parts
    if len(parts) < 2:
        return None
    title, year, hinted = _title_folder(parts[1])
    tmdb_id = hinted or item.get("tmdb_id")
    if not tmdb_id:
        return None
    if parts[0] == drives.MOVIES:
        return {"kind": "movie", "tmdb_id": tmdb_id, "title": title, "year": year, "bytes": item.get("size_bytes") or 0}
    if parts[0] != drives.TV_SHOWS:
        return None
    season, episode = item.get("season_number"), item.get("episode_number")
    if season is None or episode is None or hinted not in (None, item.get("tmdb_id")):
        found = _EPISODE.search(parts[-1])
        if found is None:
            return None
        season, episode = int(found.group(1)), int(found.group(2))
    return {"kind": "tv", "tmdb_id": tmdb_id, "title": title, "year": year, "season": season, "episode": episode,
            "bytes": item.get("size_bytes") or 0, "from_pack": item.get("media_type") == "pack"}


def lost(store: RequestStore, drive_id: str) -> dict:
    """What this app filed on the drive, by title."""
    top = drive_folder(drive_id)
    items = store.library_items_under(top)
    movies: dict[int, dict] = {}
    shows: dict[int, dict] = {}
    unknown = 0
    total = 0
    for item in items:
        parsed = _parse(item, top)
        if parsed is None:
            unknown += 1
            continue
        total += parsed["bytes"]
        if parsed["kind"] == "movie":
            movie = movies.setdefault(parsed["tmdb_id"], {
                "tmdb_id": parsed["tmdb_id"], "title": parsed["title"], "year": parsed["year"], "files": 0})
            movie["files"] += 1
        else:
            show = shows.setdefault(parsed["tmdb_id"], {
                "tmdb_id": parsed["tmdb_id"], "title": parsed["title"], "year": parsed["year"], "episodes": set(),
                "packs": set()})
            show["episodes"].add((parsed["season"], parsed["episode"]))
            if parsed["from_pack"]:
                show["packs"].add(parsed["season"])
    return {
        "files": len(items),
        "bytes": total,
        "unknown_files": unknown,
        "movies": sorted(movies.values(), key=lambda m: m["title"].lower()),
        "shows": [
            {**{k: v for k, v in show.items() if k not in ("episodes", "packs")},
             "seasons": {season: sorted(e for s, e in show["episodes"] if s == season)
                         for season in sorted({s for s, _ in show["episodes"]})},
             # Seasons that came as a pack, to fetch the same way.
             "pack_seasons": sorted(show["packs"])}
            for show in sorted(shows.values(), key=lambda s: s["title"].lower())
        ],
        "_items": items,
    }


def _filed_episodes(store: RequestStore, tmdb_id: int) -> dict[int, set[int]]:
    """Every episode of a show this app has filed, wherever it is."""
    filed: dict[int, set[int]] = defaultdict(set)
    for item in store.get_library_items(tmdb_id):
        if item.get("season_number") is not None and item.get("episode_number") is not None:
            filed[item["season_number"]].add(item["episode_number"])
            continue
        found = _EPISODE.search(Path(item["path"]).name)
        if found:
            filed[int(found.group(1))].add(int(found.group(2)))
    return filed


def download_again(store: RequestStore, tmdb: TMDBClient, drive_id: str, server_id: int,
                   plex_user_id: str | None, username: str | None) -> dict:
    """Asks for everything that was on the drive again. Returns the new
    requests' ids to queue, and what couldn't be asked for (a title TMDB
    no longer knows)."""
    found = lost(store, drive_id)
    requested: list[int] = []
    joined = 0
    failed: list[str] = []
    unplaced: set[tuple[str, int]] = set()

    for movie in found["movies"]:
        try:
            identity = resolve(movie["tmdb_id"], tmdb)
        except TMDBError:
            failed.append(movie["title"])
            unplaced.add(("movie", movie["tmdb_id"]))
            continue
        live = store.find_live_request(movie["tmdb_id"], "movie")
        if live is not None:
            store.add_request_ask(live.id, server_id, plex_user_id, username)
            joined += 1
            continue
        row = store.create_request(
            tmdb_id=movie["tmdb_id"], title=identity.title, release_year=identity.release_year, query=None,
            requested_by_plex_id=plex_user_id, requested_by_username=username,
            poster_path=identity.poster_path, server_id=server_id,
        )
        requested.append(row.id)

    for lost_show in found["shows"]:
        try:
            identity = resolve_show(lost_show["tmdb_id"], tmdb)
        except TMDBError:
            failed.append(lost_show["title"])
            unplaced.add(("tv", lost_show["tmdb_id"]))
            continue
        show = store.get_show_by_tmdb_id(lost_show["tmdb_id"]) or store.create_show(
            tmdb_id=lost_show["tmdb_id"], title=identity.title, status="paused", poster_path=identity.poster_path)
        filed = _filed_episodes(store, lost_show["tmdb_id"])
        for season, episodes in lost_show["seasons"].items():
            for episode in episodes:
                store.forget_show_episode(show.id, season, episode)
            if season in lost_show["pack_seasons"] and set(episodes) >= filed.get(season, set()):
                # The whole season was on this drive, from a pack.
                live = store.find_live_request(lost_show["tmdb_id"], "pack", season)
                if live is not None:
                    store.add_request_ask(live.id, server_id, plex_user_id, username)
                    joined += 1
                    continue
                row = store.create_pack_request(
                    tmdb_id=lost_show["tmdb_id"], show_id=show.id, title=identity.title, season_number=season,
                    requested_by_plex_id=plex_user_id, requested_by_username=username,
                    poster_path=identity.poster_path, server_id=server_id,
                )
                requested.append(row.id)
                continue
            for episode in episodes:
                live = store.find_live_request(lost_show["tmdb_id"], "episode", season, episode)
                if live is not None:
                    store.add_request_ask(live.id, server_id, plex_user_id, username)
                    joined += 1
                    continue
                row = store.create_episode_request(
                    tmdb_id=lost_show["tmdb_id"], show_id=show.id, title=identity.title, season_number=season,
                    episode_number=episode, poster_path=identity.poster_path,
                    requested_by_plex_id=plex_user_id, requested_by_username=username, server_id=server_id,
                )
                store.add_show_episode(show.id, season, episode, row.id)
                requested.append(row.id)

    # The files are gone: their ledger rows go with them, so the drive
    # lists nothing more to fetch, and the titles' pages stop counting
    # files that aren't there. A title TMDB couldn't place keeps its rows,
    # to be tried again.
    top = drive_folder(drive_id)
    for item in found["_items"]:
        parsed = _parse(item, top)
        if parsed is not None and (parsed["kind"], parsed["tmdb_id"]) in unplaced:
            continue
        store.remove_library_item(item["path"])

    settings = store.get_settings()
    drop = box_plex.dropped_folders(settings)
    store.update_settings({"box_plex_drop": drop + [f for f in library_folders(drive_id) if f not in drop]})
    logger.info("drive %s: %d title request(s) made again, %d joined, %d couldn't be (%s)",
                drive_id, len(requested), joined, len(failed), ", ".join(failed) or "none")
    return {"requested": requested, "joined": joined, "failed": failed}
