"""One title's detail pages — movie and show — and the hero trailers
cached for them."""

import re
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response as RawResponse

from app import config, logos, trailers
from app.cache import TTLCache
from app.db import RequestStore
from app.plex import local_file_for_title, plex_show_episodes
from app.tmdb import TMDBClient, TMDBError, best_logo_path, is_movie_coming_soon, is_tv_upcoming, trailer_candidates
from app.api.deps import get_store, get_tmdb, router
from app.api.helpers import _annotate_on_plex, _on_plex_for


# Cache filenames are always trailers.cached_trailer_path()'s own
# "{media_type}-{tmdb_id}-{key}.mp4" shape — validated before ever touching
# the filesystem so a crafted filename can't path-traverse out of
# TRAILER_CACHE_DIR.
_TRAILER_FILENAME_RE = re.compile(r"^[a-z]+-\d+-[\w-]+\.mp4$")


# Whether Plex can point at a file for a title this app didn't add — two
# live Plex calls, and when Plex's path doesn't exist here, a walk of the
# whole movie library. Kept per title for a while: it only decides whether
# the page offers "Replace it", and the redownload itself looks again
# before touching anything. Keyed on the store too, so each store (one per
# test) has its own answers.
_PLEX_FILE_TTL_SECONDS = 10 * 60
_plex_file_cache = TTLCache(_PLEX_FILE_TTL_SECONDS, max_entries=1024)


def _plex_file_available(store: RequestStore, title: str, year: int | None, tmdb_id: int) -> bool:
    key = (store, tmdb_id, title, year)
    available, hit = _plex_file_cache.get(key)
    if not hit:
        available = local_file_for_title(store, "movie", title, year, tmdb_id) is not None
        _plex_file_cache.set(key, available)
    return available


def _aired_episode_count(show: dict) -> int:
    """Episodes TMDB says have aired: every season before the one the
    last-aired episode is in, in full, plus that episode's number.
    Specials (season 0) don't count."""
    last = show.get("last_episode_to_air") or {}
    last_season = last.get("season_number")
    last_episode = last.get("episode_number")
    if not last_season or not last_episode:
        return 0
    before = sum(
        int(s.get("episode_count") or 0)
        for s in show.get("seasons") or []
        if 1 <= int(s.get("season_number") or 0) < last_season
    )
    return before + int(last_episode)


def _plex_episode_count(store: RequestStore, title: str, year: int | None, tmdb_id: int | None = None) -> int | None:
    """How many episodes of this show Plex has, or None when Plex isn't
    linked or can't find the show.

    Distinct (season, episode) pairs from Plex's own episode listing —
    the same thing the episode list counts — rather than a tally of
    files: an episode re-downloaded at a better quality replaces the one
    that was there, so it is still one episode however many releases it
    took to get it. Specials are left out for the same reason
    `_aired_episode_count` leaves them out: nothing else on the show page
    counts them as episodes of a season."""
    episodes = plex_show_episodes(store, title, year, tmdb_id)
    if episodes is None:
        return None
    return sum(1 for season, _episode in episodes if season >= 1)


@router.get("/api/movies/{tmdb_id}")
def get_movie_detail(
    tmdb_id: int, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    """Full TMDB detail for the detail view — overview, runtime, genres,
    poster/backdrop paths. The frontend hotlinks poster/backdrop images
    straight from TMDB's CDN using the paths returned here."""
    try:
        movie = tmdb.get_movie(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
    year_str = (movie.get("release_date") or "")[:4]
    year = int(year_str) if year_str.isdigit() else None
    # get_movie's append_to_response=release_dates already fetched exactly
    # the data is_movie_coming_soon needs — no second TMDB call. Coming
    # Soon titles use this to grey out their own Add to Plex button.
    release_dates = movie.get("release_dates", {}).get("results", [])
    is_coming_soon = is_movie_coming_soon(movie, release_dates)
    on_plex = _on_plex_for(movie.get("title") or "", year, "movie", store, tmdb_id)
    tracked = bool(store.get_library_items(tmdb_id, "movie")) or store.get_latest_organized_request(tmdb_id, ("movie",)) is not None
    collection = _collection_for(movie, tmdb, store)
    detail = {
        **movie,
        **_recommendations_on_plex(movie, "movie", store, title_key="title", date_key="release_date"),
    }
    if collection:
        # A sequel already in the franchise row would only appear twice.
        in_collection = {part.get("id") for part in collection["parts"]}
        recs = detail.get("recommendations")
        if isinstance(recs, dict):
            detail["recommendations"] = {
                **recs,
                "results": [r for r in recs.get("results") or [] if r.get("id") not in in_collection],
            }
    return {
        **detail,
        "collection": collection,
        "on_plex": on_plex,
        "is_coming_soon": is_coming_soon,
        "logo_path": _warmed(best_logo_path(movie.get("images"))),
        # Frontend migration Part K2 — true only when this app has a
        # confirmed record of having organized a file for this title
        # itself, never derived from the same fuzzy on_plex title/year
        # match above. Drives whether "Overwrite existing" is even
        # offered in the redownload confirmation modal.
        "on_plex_tracked": tracked,
        # The "On disk" tiles. Read from the library ledger rather than a
        # completed request's recorded winner, which is what the page used
        # to do: a request row is deleted by "Clear My Requests" and by
        # retention, so the tiles vanished while the file was still very
        # much on disk. The ledger outlives history, and its size is the
        # real on-disk one rather than the torrent's advertised size.
        "library": store.library_summary(tmdb_id, ("movie",)),
    }


@router.get("/api/movies/{tmdb_id}/plex-file")
def get_movie_plex_file(
    tmdb_id: int, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    """Whether there is a file this app didn't add that Plex can point at
    from here: enough for the page to offer "Replace it" and "This copy is
    broken" for it.

    Its own call, which the page makes once it has drawn. It used to ride
    in the detail itself, and so held up every view of every title already
    on Plex (most of the library, which predates this app) for its Plex
    round trips — ~0.5s measured — when only the redownload dialog reads
    it."""
    try:
        movie = tmdb.get_movie(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
    title = movie.get("title") or ""
    year_str = (movie.get("release_date") or "")[:4]
    year = int(year_str) if year_str.isdigit() else None
    tracked = bool(store.get_library_items(tmdb_id, "movie")) or store.get_latest_organized_request(tmdb_id, ("movie",)) is not None
    if tracked or not _on_plex_for(title, year, "movie", store, tmdb_id):
        return {"available": False}
    return {"available": _plex_file_available(store, title, year, tmdb_id)}


def _warmed(logo_path: str | None) -> str | None:
    """The banner logo's path, its small encoding started on the way past
    (see logos.py) so it is ready by the time the page asks for it."""
    logos.warm(logo_path)
    return logo_path


@router.get("/api/logos/{filename}")
def get_logo(filename: str):
    """A banner logo fitted to the banner and stored as WebP (logos.py).
    When that can't be had within a few seconds, the browser is sent to
    the original instead — never a missing logo."""
    name = f"{filename.removesuffix('.webp')}.png" if filename.endswith(".webp") else ""
    if not logos.is_logo_name(name):
        raise HTTPException(status_code=404, detail="logo not found")
    path = logos.get(name)
    if path is None:
        return RedirectResponse(f"/img/original/{name}", status_code=307, headers={"Cache-Control": "no-store"})
    # The name is TMDB's own content hash, so the bytes under it never change.
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _trailer_answer(media_type: str, tmdb_id: int, videos: list[dict]) -> dict:
    """The clip's URL when it is on disk. Otherwise `url: null`, with
    `pending: true` while it downloads in the background: the page shows
    the poster and asks again, rather than holding this request open for
    the download (seconds, sometimes a minute) as it used to."""
    path, pending = trailers.resolve_without_waiting(media_type, tmdb_id, trailer_candidates(videos))
    return {"url": f"/api/trailers/{path.name}" if path else None, "pending": pending}


@router.get("/api/movies/{tmdb_id}/trailer")
def get_movie_trailer(tmdb_id: int, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """Backs the hero carousel's background video — a separate call from
    get_movie_detail rather than another append_to_response, since it is
    only ever fetched for the handful of titles in a hero, not every
    movie the frontend touches. `url: null`
    (never a 404) when nothing suitable is on file or the download fails —
    a title with no trailer is a normal, expected case, not an error the
    caller needs to handle specially; it just falls back to a plain
    poster/backdrop. Downloads and serves the clip from our own cache
    (trailers.py) rather than embedding YouTube's player — see that
    module's docstring for why, and `trailers.resolve` for which of a
    title's clips gets picked."""
    try:
        videos = tmdb.get_movie_videos(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _trailer_answer("movie", tmdb_id, videos)


def _collection_for(movie: dict, tmdb: TMDBClient, store: RequestStore) -> dict | None:
    """The franchise this film belongs to — every other film in TMDB's
    collection for it, in release order, each carrying `on_plex` — or
    None when it stands alone.

    Release order rather than TMDB's own, which is whatever order parts
    were added in. The film itself is left out: the row is "what else is
    there", and the page is already this one.

    Only films that exist, or plainly will. TMDB lists a sequel the day
    it is rumoured — "Untitled National Treasure 3", no date, years on —
    and a row that offers it reads as a promise. A part is kept once it
    is out, or when it has a date and a trailer: a studio cutting a
    trailer is the point a sequel is really coming. A part with a date
    alone stays out, since dates on unmade sequels are placeholders.

    Never fails the page: a collection that can't be fetched is a page
    without the row, the same as a film without a franchise."""
    belongs = movie.get("belongs_to_collection")
    if not isinstance(belongs, dict) or not belongs.get("id"):
        return None
    try:
        collection = tmdb.get_collection(int(belongs["id"]))
    except TMDBError:
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    parts = [
        p
        for p in collection.get("parts") or []
        if p.get("id") != movie.get("id") and _part_is_real(p, today, tmdb)
    ]
    if not parts:
        return None
    parts.sort(key=lambda p: p.get("release_date") or "")
    return {
        "id": belongs["id"],
        "name": collection.get("name") or belongs.get("name") or "",
        "parts": _annotate_on_plex(parts, "movie", store, title_key="title", date_key="release_date"),
    }


def _part_is_real(part: dict, today: str, tmdb: TMDBClient) -> bool:
    """See _collection_for: out already, or dated with a trailer."""
    released = part.get("release_date") or ""
    if not released:
        return False
    if released <= today:
        return True
    try:
        return bool(trailer_candidates(tmdb.get_movie_videos(int(part["id"]))))
    except (TMDBError, KeyError, TypeError, ValueError):
        return False


def _recommendations_on_plex(detail: dict, media_type: str, store: RequestStore, *, title_key: str, date_key: str) -> dict:
    """The detail's "More like this" titles, carrying `on_plex` like every
    other row. They arrive inside the detail's own append_to_response,
    not through a list route, so they were the one row the annotation
    never reached and never showed the On Plex badge. Returned as a key
    to merge (empty when TMDB sent none), built on copies: get_movie and
    get_tv are cached, and _annotate_on_plex never mutates."""
    recs = detail.get("recommendations")
    if not isinstance(recs, dict):
        return {}
    results = _annotate_on_plex(recs.get("results") or [], media_type, store, title_key=title_key, date_key=date_key)
    return {"recommendations": {**recs, "results": results}}


@router.get("/api/tv/{tmdb_id}")
def get_tv_detail(tmdb_id: int, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """Full TMDB show detail — overview, seasons, status (Returning
    Series/Ended/Canceled), genres, poster/backdrop paths. Backs the show
    detail view's subscribe/unsubscribe/bulk-download controls."""
    try:
        show = tmdb.get_tv(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
    year_str = (show.get("first_air_date") or "")[:4]
    year = int(year_str) if year_str.isdigit() else None
    on_plex = _on_plex_for(show.get("name") or "", year, "show", store, tmdb_id)
    # Every aired episode is already on Plex: the show page hides "Add
    # all to Plex" rather than offering a download that would add nothing.
    aired = _aired_episode_count(show)
    have = _plex_episode_count(store, show.get("name") or "", year, tmdb_id) if on_plex else None
    return {
        **show,
        **_recommendations_on_plex(show, "show", store, title_key="name", date_key="first_air_date"),
        "on_plex": on_plex,
        "plex_complete": bool(have is not None and have >= aired > 0),
        # How many episodes the household actually has, for the "On disk"
        # tiles. The ledger below counts files, which counts a
        # re-downloaded episode twice; this counts episodes.
        "plex_episode_count": have,
        "is_coming_soon": is_tv_upcoming(show),
        "logo_path": _warmed(best_logo_path(show.get("images"))),
        # Frontend migration Part K3 — TV parity with the movie route
        # above. A show's organized history is episode/pack rows, never
        # a single fixed media_type the way a movie's always is.
        "on_plex_tracked": store.get_latest_organized_request(tmdb_id, ("episode", "pack")) is not None,
        # The show-level equivalent of the movie page's "File" tiles.
        # Rolled up here rather than per-episode: a show's quality and
        # disk footprint are properties of the whole run, and episode
        # rows are both media types a show can be filed under.
        "library": store.library_summary(tmdb_id, ("episode", "pack")),
    }


@router.get("/api/tv/{tmdb_id}/trailer")
def get_tv_trailer(tmdb_id: int, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """The TV equivalent of get_movie_trailer above — same reasoning."""
    try:
        videos = tmdb.get_tv_videos(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _trailer_answer("tv", tmdb_id, videos)


@router.get("/api/trailers/{filename}")
def get_trailer_file(filename: str) -> FileResponse:
    """Serves a cached hero-carousel trailer downloaded by trailers.py.
    Filename is regex-whitelisted before it ever reaches the filesystem —
    it's a path segment taken straight from the URL."""
    if not _TRAILER_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="trailer not found")
    path = config.TRAILER_CACHE_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="trailer not found")
    if config.TRAILER_X_ACCEL_PREFIX:
        # Everything above still runs — the session gate on this router,
        # the filename whitelist, the existence check — and only then is
        # the file handed to nginx to actually send. A hero playing five
        # of these otherwise has uvicorn streaming video while it is
        # also the thing answering the API, and it serves ranges worse
        # than nginx does besides. The body is empty on purpose: nginx
        # discards it and sends the file named by the header.
        return RawResponse(
            status_code=200,
            media_type="video/mp4",
            headers={"X-Accel-Redirect": f"{config.TRAILER_X_ACCEL_PREFIX.rstrip('/')}/{filename}"},
        )
    return FileResponse(path, media_type="video/mp4")
