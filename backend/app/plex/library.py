"""What's on Plex, read from the linked servers: the "On Plex" lookup the
grids use, a title's files and a show's episodes, each person's watch
history, and the checks the worker makes across every server before it
calls a download gone. Plus the refresh after an import.

Per "fail safe, not best guess": a torrent that vanishes from qBittorrent
is only called Cancelled once Plex has also failed to find the title — see
the worker's `_check_downloading`."""

import logging
import time
from collections.abc import Callable
from pathlib import Path

import requests

from app.box import drives
from app.core.cache import TTLCache
from app.db import PlexServerRow
from app.plex.client import LibraryIndex, PlexClient, PlexError, client_from_settings

logger = logging.getLogger(__name__)


# One cache per (server_url, server_token, media_type) — a fresh PlexClient
# is constructed per call below (matching has_in_library's existing
# pattern), so `app.cache.ttl_cache`'s self-keying decorator would never
# actually hit; this is keyed on the request's own identity instead.
_LIBRARY_INDEX_TTL_SECONDS = 120


_library_index_cache = TTLCache(_LIBRARY_INDEX_TTL_SECONDS)


def _cached_library_index(store, server: PlexServerRow | None, media_type: str) -> LibraryIndex | None:
    """The cached whole-library snapshot of `server`, or None if no
    server is linked. A Plex hiccup yields an empty index: browsing must
    not break."""
    if server is None:
        return None
    cache_key = (server.url, server.token, media_type)
    index, hit = _library_index_cache.get(cache_key)
    if not hit:
        client = client_from_settings(store.get_settings())
        try:
            index = client.library_index(server.url, server.token, media_type)
        except (PlexError, requests.RequestException):
            # A server that can't be reached at all, too — not just one
            # that answers badly.
            index = LibraryIndex([])
        _library_index_cache.set(cache_key, index)
    return index


def plex_library_lookup(store, server: PlexServerRow | None, media_type: str) -> Callable[..., bool] | None:
    """Returns a matcher `(title, year, tmdb_id=None) -> bool` backed by one cached,
    whole-library snapshot, or `None` if Plex isn't linked. Meant to be
    called once per API request (not once per item) so a whole page of
    discover/search results can be annotated with a single Plex round trip
    every ~2 minutes rather than one per item. The caller resolves
    `server` per request (Plex can be linked/unlinked while the backend
    is running), same as `has_in_library`."""
    index = _cached_library_index(store, server, media_type)
    if index is None:
        return None

    def matcher(title: str, year: int | None, tmdb_id: int | None = None) -> bool:
        return index.find(title, year, tmdb_id) is not None

    return matcher


# Watch history changes when someone finishes something, not per page
# load; ten minutes keeps Home quick without leaving a finished film out of
# the next visit's taste for long.
_WATCH_HISTORY_TTL_SECONDS = 600


_watch_history_cache = TTLCache(_WATCH_HISTORY_TTL_SECONDS)


def user_watch_history(store, server: PlexServerRow | None, plex_user_id: str, is_admin: bool) -> dict[str, list[dict]]:
    """This person's watched movies and shows (`{"movie": [...], "tv":
    [...]}`, keyed by TMDB's namespaces), from Plex, cached per person.

    Asked with their own server token, captured at sign-in — the same
    rule, and the same admin fallback, as the Continue watching row (see
    api/plex.py's get_plex_on_deck): the admin's token would describe the
    admin's viewing, not theirs. Empty rather than an error when Plex isn't
    linked, the token isn't known yet, or Plex doesn't answer: it feeds
    recommendations, and those degrade to the other signals."""
    if server is None:
        return {"movie": [], "tv": []}
    server_url = server.url
    token = store.get_user_server_token(plex_user_id, server.id)
    if not token and is_admin:
        token = server.token
    if not token:
        return {"movie": [], "tv": []}
    cache_key = (server_url, plex_user_id, token)
    history, hit = _watch_history_cache.get(cache_key)
    if hit:
        return history
    client = client_from_settings(store.get_settings())
    history = {"movie": [], "tv": []}
    for plex_type, tmdb_type in (("movie", "movie"), ("show", "tv")):
        try:
            history[tmdb_type] = client.watched(server_url, token, plex_type)
        except (PlexError, requests.RequestException, ValueError) as exc:
            logging.getLogger(__name__).info("plex watch history unavailable (%s): %s", plex_type, exc)
    _watch_history_cache.set(cache_key, history)
    return history


def has_in_library(store, server: PlexServerRow | None, title: str, year: int | None, tmdb_id: int | None = None) -> bool | None:
    """None if Plex isn't linked (caller falls back to its own default);
    True/False once we can actually check. The caller resolves `server`
    fresh each time rather than holding on to one, since the household
    can link/unlink while the backend is running."""
    if server is None:
        return None
    server_url, server_token = server.url, server.token
    client = client_from_settings(store.get_settings())
    if tmdb_id is not None:
        # A fresh whole-library read: the title-filtered search can't find
        # a movie Plex spells differently, and a cached one may predate it.
        return client.library_index(server_url, server_token, "movie").find(title, year, tmdb_id) is not None
    return client.has_movie(server_url, server_token, title, year)


# -- Asking every server. The worker's questions are about the library,
#    not about anyone's view of it, and every server reads the same mounts
#    — but each only sees the libraries it was set up with, so one server
#    not having a title isn't the library not having it. --


def has_in_any_library(store, title: str, year: int | None, tmdb_id: int | None = None) -> bool | None:
    """has_in_library across every linked server: True once any has it,
    None when none is linked. A server that couldn't be asked only
    matters when no other server has it — then its error is raised, as
    has_in_library's would be, rather than read as "not there"."""
    answers: list[bool | None] = []
    failure: Exception | None = None
    for server in store.list_plex_servers():
        try:
            found = has_in_library(store, server, title, year, tmdb_id)
        except Exception as exc:  # noqa: BLE001 — only decisive if nobody has it
            failure = failure or exc
            continue
        if found:
            return True
        answers.append(found)
    if failure is not None:
        raise failure
    return False if any(a is not None for a in answers) else None


def local_file_on_any_server(
    store, media_type: str, title: str, year: int | None, tmdb_id: int | None = None
) -> Path | None:
    """local_file_for_title from the first server that can point at one."""
    for server in store.list_plex_servers():
        found = local_file_for_title(store, server, media_type, title, year, tmdb_id)
        if found is not None:
            return found
    return None


def show_episodes_on_any_server(
    store, title: str, year: int | None, tmdb_id: int | None = None
) -> set[tuple[int, int]] | None:
    """plex_show_episodes from every server together: an episode any of
    them holds is one the household has. None when none has the show."""
    found: set[tuple[int, int]] | None = None
    for server in store.list_plex_servers():
        episodes = plex_show_episodes(store, server, title, year, tmdb_id)
        if episodes is not None:
            found = (found or set()) | episodes
    return found


def refresh_after_import(store, server: PlexServerRow | None, media_type: str, organized_path: str | None) -> bool:
    """Settings › Plex "Refresh Plex after import": scans the movie or show
    library once a file has been placed. The organized path is passed as
    a partial scan when it sits under one of the section's own locations
    (Plex sees the same mount), otherwise the whole section is refreshed.
    Best effort — a failure is logged, never raised into the worker."""
    settings = store.get_settings()
    if settings.get("plex_refresh_after_import", True) is False:
        return False
    if server is None:
        return False
    url, token = server.url, server.token
    client = client_from_settings(settings)
    wanted = "movie" if media_type == "movie" else "show"
    try:
        sections = [s for s in client.sections(url, token) if s.get("type") == wanted]
        if not sections:
            return False
        for section in sections:
            partial = None
            if organized_path and any(organized_path.startswith(loc.rstrip("/") + "/") for loc in section["locations"]):
                partial = organized_path
            client.refresh_section(url, token, section["key"], partial)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("plex refresh after import skipped: %s", exc)
        return False


# Plex as the verifier of what a household actually has of a show: the
# worker's follow check and pack fallbacks ask this before requesting
# anything, so an episode already on the server is marked done rather
# than fetched again — whatever the request ledger says about it.
_show_episodes_cache: dict[tuple[str, str, int | None], tuple[float, set[tuple[int, int]] | None]] = {}


_SHOW_EPISODES_TTL_SECONDS = 60.0


def plex_show_episodes(store, server: PlexServerRow | None, title: str, year: int | None, tmdb_id: int | None = None) -> set[tuple[int, int]] | None:
    """The (season, episode) pairs Plex holds for `title`, or None when
    Plex isn't linked or doesn't have the show at all. Cached a minute
    per show so a check that walks every season asks once."""
    if server is None:
        return None
    server_url, server_token = server.url, server.token
    key = (server_url, title, year, tmdb_id)
    hit = _show_episodes_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _SHOW_EPISODES_TTL_SECONDS:
        return hit[1]
    client = client_from_settings(store.get_settings())
    try:
        item = locate_title(store, server, client, "show", title, year, tmdb_id)
        episodes = client.show_episodes(server_url, server_token, item["rating_key"]) if item else None
    except (PlexError, requests.RequestException):
        episodes = None
    _show_episodes_cache[key] = (now, episodes)
    return episodes


def locate_title(
    store, server: PlexServerRow, client: "PlexClient", media_type: str, title: str, year: int | None, tmdb_id: int | None = None
) -> dict | None:
    """`PlexClient.locate`, falling back to the cached library index by
    TMDB id: Plex's title-filtered search misses a title it spells
    differently from TMDB."""
    found = client.locate(server.url, server.token, media_type, title, year, tmdb_id)
    if found is None and tmdb_id is not None:
        index = _cached_library_index(store, server, media_type)
        item = index.find(title, year, tmdb_id) if index else None
        if item and item.get("tmdb_id") == tmdb_id and item.get("rating_key"):
            found = {"rating_key": item["rating_key"], "title": item["title"], "year": item["year"]}
    return found


def plex_title_files(
    store, server: PlexServerRow | None, media_type: str, title: str, year: int | None, tmdb_id: int | None = None
) -> list[str] | None:
    """The file paths Plex holds for a title, or None when Plex isn't
    linked or doesn't have it."""
    if server is None:
        return None
    client = client_from_settings(store.get_settings())
    try:
        item = locate_title(store, server, client, media_type, title, year, tmdb_id)
        return client.file_paths(server.url, server.token, item["rating_key"]) if item else None
    except (PlexError, requests.RequestException):
        return None


def local_file_for_title(
    store, server: PlexServerRow | None, media_type: str, title: str, year: int | None, tmdb_id: int | None = None
) -> Path | None:
    """Where a title's file (per Plex) is from this app's point of view.
    Plex's own path is used when it exists here; otherwise the file is
    looked up by name under the library root, since Plex and this app
    may mount the same library at different paths. None when Plex has
    no file, or it can't be found from here — nothing is ever deleted
    on a guess."""
    paths = plex_title_files(store, server, media_type, title, year, tmdb_id)
    if not paths:
        return None
    roots = drives.library_roots("movie" if media_type == "movie" else "tv")
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
        for root in roots:
            if root.is_dir():
                for found in root.rglob(candidate.name):
                    if found.is_file():
                        return found
    return None
