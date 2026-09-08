"""FastAPI wrapper around Stages 1-2's library code, plus the SQLite job
store. Same-origin only (confirmed architecture) — no CORS middleware; the
frontend (Stage 4) reaches this only via nginx's reverse proxy on the same
origin. TMDB key and qBittorrent credentials never reach the browser: every
route here is either a thin TMDB proxy or reads/writes the local job store."""

import logging
import shutil
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app import config
from app.db import RequestRow, RequestStore, ShowRow
from app.deploy import DeployError, run_git_pull
from app.logging_config import configure_logging
from app.pipeline_settings import (
    VALID_MIN_RESOLUTIONS,
    is_valid_min_resolution,
    resolve_pipeline_settings,
    settings_from_raw,
)
from app.plex import PlexError, PlexLinker, plex_library_lookup
from app.qbt import QBTClient
from app.resolve import resolve
from app.tmdb import TMDBClient, TMDBError, is_movie_coming_soon, is_tv_upcoming
from app.tv_resolve import resolve_show
from app.tv_settings import resolve_tv_settings
from app.worker import Worker

configure_logging()
logger = logging.getLogger("app.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = RequestStore(config.DB_PATH)
    app.state.tmdb = TMDBClient(config.TMDB_API_KEY)
    app.state.qbt = QBTClient(config.QBIT_HOST, config.QBIT_PORT, config.QBIT_USERNAME, config.QBIT_PASSWORD)
    app.state.worker = Worker(app.state.store, app.state.tmdb, app.state.qbt)
    app.state.plex_linker = PlexLinker(app.state.store)
    await app.state.worker.start()
    try:
        yield
    finally:
        await app.state.worker.stop()
        app.state.store.close()


app = FastAPI(title="The Family Downloader", lifespan=lifespan)


def get_store(request: Request) -> RequestStore:
    return request.app.state.store


def get_tmdb(request: Request) -> TMDBClient:
    return request.app.state.tmdb


def get_worker(request: Request) -> Worker:
    return request.app.state.worker


def get_qbt(request: Request) -> QBTClient:
    return request.app.state.qbt


def get_plex_linker(request: Request) -> PlexLinker:
    return request.app.state.plex_linker


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    year: int | None = None
    provider_id: int | None = None


class CreateRequest(BaseModel):
    tmdb_id: int
    query: str | None = None


class RetentionSettings(BaseModel):
    days: int | None = None  # None/0 = keep forever


class PipelineSettingsIn(BaseModel):
    """Same "always send the full desired state, null = reset to the
    config.py default" convention as RetentionSettings above — no
    partial-patch merging to worry about. Sanity validation
    (Stage 7's second open decision) catches an edit that would otherwise
    silently degrade every future request to "no qualifying results"."""

    category: str | None = Field(default=None, min_length=1)
    min_resolution: str | None = None
    min_size_gb: float | None = Field(default=None, gt=0)
    max_size_gb: float | None = Field(default=None, gt=0)
    language_allowlist: list[str] | None = None
    language_blocklist: list[str] | None = None
    language_required: list[str] | None = None

    @field_validator("min_resolution")
    @classmethod
    def _known_resolution(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_min_resolution(v):
            raise ValueError(f"min_resolution must be one of {VALID_MIN_RESOLUTIONS}")
        return v

    @model_validator(mode="after")
    def _size_range_is_sane(self) -> "PipelineSettingsIn":
        if self.min_size_gb is not None and self.max_size_gb is not None and self.min_size_gb >= self.max_size_gb:
            raise ValueError("min_size_gb must be less than max_size_gb")
        return self


class TVScheduleSettingsIn(BaseModel):
    """Same "always send the full desired state, null = reset to the
    config.py default" convention as every other settings model. 0 is a
    valid, deliberate `episode_recheck_max_attempts` (infinite) — only
    negative values are rejected."""

    show_check_interval_hours: float | None = Field(default=None, gt=0)
    episode_recheck_enabled: bool | None = None
    episode_recheck_interval_hours: float | None = Field(default=None, gt=0)
    episode_recheck_max_attempts: int | None = Field(default=None, ge=0)
    episode_air_buffer_hours: float | None = Field(default=None, ge=0)


class RequestOut(BaseModel):
    id: int
    query: str | None
    tmdb_id: int
    title: str
    release_year: int | None
    status: str
    error_message: str | None
    result: dict | None
    created_at: str
    updated_at: str
    media_type: str
    show_id: int | None
    season_number: int | None
    episode_number: int | None
    season_range_end: int | None

    @classmethod
    def from_row(cls, row: RequestRow) -> "RequestOut":
        return cls(**row.__dict__)


class SubscribeShowRequest(BaseModel):
    tmdb_id: int


class BulkDownloadRequest(BaseModel):
    """Stage 13: `{"scope": "series"}` or `{"scope": "season", "season_number": N}`.
    Deliberately its own request, distinct from `SubscribeShowRequest` — a
    bulk download is a one-shot user action, independent of (not a
    replacement for) the standing per-episode subscription."""

    scope: str
    season_number: int | None = None

    @field_validator("scope")
    @classmethod
    def _known_scope(cls, v: str) -> str:
        if v not in ("season", "series"):
            raise ValueError("scope must be 'season' or 'series'")
        return v

    @model_validator(mode="after")
    def _season_number_matches_scope(self) -> "BulkDownloadRequest":
        if self.scope == "season" and self.season_number is None:
            raise ValueError("season_number is required when scope is 'season'")
        if self.scope == "series" and self.season_number is not None:
            raise ValueError("season_number must not be set when scope is 'series'")
        return self


class ShowOut(BaseModel):
    id: int
    tmdb_id: int
    title: str
    status: str
    created_at: str
    last_checked_at: str | None
    # Stage 14: the Watching list's "latest episode status" — the most
    # recent episode/pack request this show has produced, or None if it
    # hasn't been checked yet (e.g. just subscribed, catch-up still queued).
    latest_request: RequestOut | None = None

    @classmethod
    def from_row(cls, row: ShowRow, latest_request: RequestOut | None = None) -> "ShowOut":
        return cls(**row.__dict__, latest_request=latest_request)


def _show_out(store: RequestStore, row: ShowRow) -> ShowOut:
    latest = store.get_latest_request_for_show(row.id)
    return ShowOut.from_row(row, latest_request=RequestOut.from_row(latest) if latest else None)


# ---------------------------------------------------------------------------
# Stage 14: "On Plex" badge — annotates already-fetched TMDB result lists
# (and single-item detail responses) with `on_plex`, using one cached,
# whole-library Plex lookup per request rather than one live Plex call per
# item. See plex.py's `plex_library_lookup` for the caching design.
# ---------------------------------------------------------------------------


def _annotate_on_plex(
    results: list[dict], media_type: str, store: RequestStore, *, title_key: str, date_key: str
) -> list[dict]:
    """Returns a *new* list of *new* dicts, `on_plex` added to each — never
    mutates the input items in place. Several of tmdb.py's methods
    (popular/trending/discover-by-provider/coming-soon) are TTL-cached and
    hand back the same item dict object on every cache hit; mutating those
    in place would bake one request's Plex-link state into the shared
    cache for every later hit until the (independent) TMDB cache itself
    expires."""
    matcher = plex_library_lookup(store, media_type)
    annotated = []
    for item in results:
        year_str = (item.get(date_key) or "")[:4]
        year = int(year_str) if year_str.isdigit() else None
        on_plex = bool(matcher(item.get(title_key) or "", year)) if matcher else False
        annotated.append({**item, "on_plex": on_plex})
    return annotated


def _on_plex_for(title: str, year: int | None, media_type: str, store: RequestStore) -> bool:
    matcher = plex_library_lookup(store, media_type)
    return bool(matcher(title, year)) if matcher else False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/api/search")
def search(
    body: SearchRequest, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> list[dict]:
    try:
        if body.provider_id is not None:
            data = tmdb.search_within_provider(body.query, body.provider_id)
        else:
            data = tmdb.search_movie(body.query, year=body.year)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    results = data.get("results", [])
    return _annotate_on_plex(results, "movie", store, title_key="title", date_key="release_date")


# -- Stage 4: the browse surface the home grid and provider rows are built
#    from. Thin pass-throughs of tmdb.py's already-TTL-cached methods —
#    same "key never reaches the browser" rule as /api/search. --


@app.get("/api/discover/popular")
def discover_popular(page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    # Digital-availability filtered: Coming Soon is the dedicated tab for
    # theatrical-only titles, so Discover shouldn't also surface them.
    try:
        data = tmdb.get_available_popular(page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@app.get("/api/discover/trending")
def discover_trending(
    time_window: str = "week", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_available_trending(time_window=time_window, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@app.get("/api/discover/providers")
def discover_providers(region: str = "US", tmdb: TMDBClient = Depends(get_tmdb)) -> list[dict]:
    try:
        data = tmdb.get_watch_providers(region=region)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data.get("results", [])


@app.get("/api/discover/providers/{provider_id}")
def discover_by_provider(
    provider_id: int,
    region: str = "US",
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    # Digital-availability filtered — same reasoning as Discover Popular/
    # Trending above: a provider row shouldn't surface a theatrical-only
    # title Coming Soon already owns.
    try:
        data = tmdb.get_available_by_provider(provider_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@app.get("/api/discover/genre/{genre_id}")
def discover_by_genre(
    genre_id: int,
    region: str = "US",
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    # Digital-availability filtered — same reasoning as Discover Popular/
    # Trending above.
    try:
        data = tmdb.get_available_by_genre(genre_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@app.get("/api/discover/coming-soon")
def discover_coming_soon(
    region: str = "US", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_coming_soon(region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@app.get("/api/movies/{tmdb_id}")
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
    is_coming_soon = is_movie_coming_soon(movie, release_dates, region="US")
    return {
        **movie,
        "on_plex": _on_plex_for(movie.get("title") or "", year, "movie", store),
        "is_coming_soon": is_coming_soon,
    }


# -- Stage 14: TV browse surface — the show equivalent of the movie routes
#    above. Same thin-pass-through/on_plex-annotation/key-never-reaches-
#    the-browser rules. --


@app.get("/api/tv/discover/popular")
def tv_discover_popular(
    page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    # Filtered to shows that have aired at least one episode — Coming Soon
    # is the dedicated place for anything that hasn't, same rule as the
    # movie side's digital-availability filter above.
    try:
        data = tmdb.get_available_tv_popular(page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@app.get("/api/tv/discover/trending")
def tv_discover_trending(
    time_window: str = "week", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_available_tv_trending(time_window=time_window, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@app.get("/api/tv/discover/providers/{provider_id}")
def tv_discover_by_provider(
    provider_id: int,
    region: str = "US",
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    try:
        data = tmdb.get_available_tv_by_provider(provider_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@app.get("/api/tv/discover/genre/{genre_id}")
def tv_discover_by_genre(
    genre_id: int,
    region: str = "US",
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    try:
        data = tmdb.get_available_tv_by_genre(genre_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@app.get("/api/tv/discover/coming-soon")
def tv_discover_coming_soon(
    region: str = "US", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_tv_coming_soon(region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@app.get("/api/tv/{tmdb_id}")
def get_tv_detail(tmdb_id: int, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """Full TMDB show detail — overview, seasons, status (Returning
    Series/Ended/Canceled), genres, poster/backdrop paths. Backs the show
    detail view's subscribe/pause/resume/bulk-download controls."""
    try:
        show = tmdb.get_tv(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
    year_str = (show.get("first_air_date") or "")[:4]
    year = int(year_str) if year_str.isdigit() else None
    return {
        **show,
        "on_plex": _on_plex_for(show.get("name") or "", year, "show", store),
        "is_coming_soon": is_tv_upcoming(show),
    }


@app.post("/api/tv/search")
def search_tv(
    body: SearchRequest, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> list[dict]:
    try:
        if body.provider_id is not None:
            data = tmdb.search_tv_within_provider(body.query, body.provider_id)
        else:
            data = tmdb.search_tv(body.query, year=body.year)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    results = data.get("results", [])
    return _annotate_on_plex(results, "show", store, title_key="name", date_key="first_air_date")


@app.post("/api/requests", status_code=201)
def create_request(
    body: CreateRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
) -> RequestOut:
    try:
        identity = resolve(body.tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {body.tmdb_id} not found") from exc

    row = store.create_request(
        tmdb_id=body.tmdb_id,
        title=identity.title,
        release_year=identity.release_year,
        query=body.query,
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


@app.get("/api/requests")
def list_requests(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[RequestOut]:
    return [RequestOut.from_row(r) for r in store.list_requests(status=status)]


@app.get("/api/requests/{request_id}")
def get_request(request_id: int, store: RequestStore = Depends(get_store)) -> RequestOut:
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    return RequestOut.from_row(row)


@app.post("/api/requests/clear")
def clear_requests(store: RequestStore = Depends(get_store)) -> dict:
    """"Clear My Requests": wipes settled history (reuses the same
    active-job-safe query the automatic retention cleanup runs, with
    days=0 so age never excludes anything terminal)."""
    return {"removed": store.purge_requests_older_than(days=0)}


# A "queued" request hasn't touched qBittorrent (or even started
# resolving/searching) at all yet, so cancelling one is a plain status
# flip — no torrent to delete. worker.py's `_run_one` already re-checks
# `status == "queued"` the moment it's dequeued (guarding against a stale
# queue entry across a restart), so a row cancelled while still sitting in
# the in-memory queue is simply skipped when the worker gets to it,
# no extra wiring needed here. "searching" is deliberately NOT
# cancellable yet — the pipeline is actively running synchronously in a
# worker thread with no cancellation hook, and cancelling the DB row out
# from under it risks the pipeline's own completion overwriting
# "cancelled" back to whatever it concluded once it finishes. A real,
# named gap, same style as this project's other not-yet-solved ones.
_TORRENT_CANCELLABLE_STATUSES = {"downloading", "complete"}


@app.post("/api/requests/{request_id}/cancel")
def cancel_request(
    request_id: int, store: RequestStore = Depends(get_store), qbt: QBTClient = Depends(get_qbt)
) -> RequestOut:
    """Cancel from the app's own UI. A "queued" request is just marked
    "cancelled" directly (see the comment above `_TORRENT_CANCELLABLE_STATUSES`).
    A "downloading"/"complete" request instead deletes the torrent *and
    its downloaded files* from qBittorrent (fail safe, not best guess —
    never silently leave orphaned media on disk), then marks it
    "cancelled". Either way the row itself stays — this is "download
    history", not a queue, per the "hidden, never unrecoverable"
    principle.

    If qBittorrent no longer has the torrent at all — most commonly
    because "remove torrent after completion" already auto-removed it —
    there is nothing left to delete, and this app has no other path to the
    file (it never mounts the download folder or the Docker socket, per
    the confirmed architecture). Rather than mark the request "cancelled"
    and imply files were removed when nothing was touched, this fails
    loudly and leaves the request exactly as it was."""
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")

    if row.status == "queued":
        store.update_status(request_id, "cancelled")
        logger.info("request %d (%s) queued -> cancelled (via API)", request_id, row.title)
        return RequestOut.from_row(store.get_request(request_id))

    if row.status not in _TORRENT_CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"cannot cancel a request in status {row.status!r}")
    torrent_hash = (row.result or {}).get("torrent_hash")
    if not torrent_hash:
        raise HTTPException(status_code=409, detail="no torrent on record for this request")
    if qbt.torrent_info(torrent_hash) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "qBittorrent no longer has this torrent — it most likely finished and was "
                "auto-removed. Nothing was deleted; if the file is still on disk, it needs to "
                "be removed manually."
            ),
        )
    qbt.delete_torrent(torrent_hash, delete_files=True)
    store.update_status(request_id, "cancelled")
    logger.info("request %d (%s) downloading/complete -> cancelled (via API)", request_id, row.title)
    return RequestOut.from_row(store.get_request(request_id))


# -- Stage 12: standing show subscriptions. POST creates the subscription
#    and immediately runs a *full* backfill catch-up (Stage 14.x — every
#    season from 1 through the current latest, not just the latest), so
#    subscribing to a show with nothing downloaded at all grabs everything
#    already aired, not only its newest season. Every subsequent scheduled
#    recheck (worker.py's `_check_all_watching_shows`) keeps checking only
#    the latest season, same as always — this full sweep only ever runs
#    once, right here, at the moment of subscribing. --


@app.post("/api/shows", status_code=201)
def create_show(
    body: SubscribeShowRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
) -> ShowOut:
    if store.get_show_by_tmdb_id(body.tmdb_id) is not None:
        raise HTTPException(status_code=409, detail="already subscribed to this show")
    try:
        identity = resolve_show(body.tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {body.tmdb_id} not found") from exc

    row = store.create_show(tmdb_id=body.tmdb_id, title=identity.title)
    worker.check_show(row, full_backfill=True)
    return _show_out(store, store.get_show(row.id))


@app.get("/api/shows")
def list_shows(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[ShowOut]:
    return [_show_out(store, r) for r in store.list_shows(status=status)]


@app.get("/api/shows/{show_id}")
def get_show(show_id: int, store: RequestStore = Depends(get_store)) -> ShowOut:
    row = store.get_show(show_id)
    if row is None:
        raise HTTPException(status_code=404, detail="show not found")
    return _show_out(store, row)


@app.post("/api/shows/{show_id}/pause")
def pause_show(show_id: int, store: RequestStore = Depends(get_store)) -> ShowOut:
    if store.get_show(show_id) is None:
        raise HTTPException(status_code=404, detail="show not found")
    store.update_show_status(show_id, "paused")
    return _show_out(store, store.get_show(show_id))


@app.post("/api/shows/{show_id}/resume")
def resume_show(show_id: int, store: RequestStore = Depends(get_store)) -> ShowOut:
    if store.get_show(show_id) is None:
        raise HTTPException(status_code=404, detail="show not found")
    store.update_show_status(show_id, "watching")
    return _show_out(store, store.get_show(show_id))


@app.delete("/api/shows/{show_id}")
def unsubscribe_show(show_id: int, store: RequestStore = Depends(get_store)) -> dict:
    """Unsubscribes — stops future checks. Every `requests`/`show_episodes`
    row this show ever produced stays exactly as it was, same "hidden,
    never unrecoverable" principle as cancelling a movie request."""
    if not store.delete_show(show_id):
        raise HTTPException(status_code=404, detail="show not found")
    return {"deleted": True}


# -- Stage 13: whole-season / complete-series bulk acquisition. A plain,
#    explicit user action distinct from subscribe/pause/resume above —
#    usable regardless of a show's watching/paused status, and regardless
#    of whether it's still airing, per the plan's "independent, not
#    mutually exclusive" call. Goes through the same requests table/worker
#    queue as every other download (`media_type='pack'`), so it never races
#    a queued movie/episode search — see worker.py's `_run_one`.
#
#    Stage 14.x: keyed by `tmdb_id`, not a local `shows.id` — bulk
#    download is meant to work whether or not the show has ever been
#    subscribed (e.g. "just grab me the one season already out, I don't
#    want a standing subscription"). If no `shows` row exists yet, one is
#    created here with status "paused" rather than "watching" — anchoring
#    the request without opting the show into the standing per-episode
#    catch-up/recheck, which only ever looks at "watching" rows. A show
#    that's already subscribed (watching or paused) is used as-is; its
#    status is never touched by this route. --


@app.post("/api/tv/{tmdb_id}/bulk-download", status_code=201)
def bulk_download_show(
    tmdb_id: int,
    body: BulkDownloadRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
) -> RequestOut:
    show = store.get_show_by_tmdb_id(tmdb_id)
    if show is None:
        try:
            identity = resolve_show(tmdb_id, tmdb)
        except TMDBError as exc:
            raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
        show = store.create_show(tmdb_id=tmdb_id, title=identity.title, status="paused")

    # A bulk download makes this show's own still-queued requests it
    # covers redundant — series scope covers every season, a season scope
    # only that same season — see db.py's cancel_queued_requests_for_show
    # for exactly what's touched (queued only; never a request already
    # searching/downloading/complete). Without this, subscribing to a show
    # (which immediately queues catch-up requests for its latest season)
    # and then asking for a bulk download of the same show left both
    # running at once, flooding the requests list with soon-redundant rows.
    cancelled = store.cancel_queued_requests_for_show(show.id, body.season_number)
    if cancelled:
        logger.info(
            "show %d (%s): bulk-download (%s) cancelled %d still-queued request(s) it supersedes",
            show.id, show.title, body.scope, cancelled,
        )

    row = store.create_pack_request(
        tmdb_id=show.tmdb_id,
        show_id=show.id,
        title=show.title,
        season_number=body.season_number,
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


@app.get("/api/settings/retention")
def get_retention(store: RequestStore = Depends(get_store)) -> dict:
    return {"days": store.get_settings().get("request_retention_days")}


@app.put("/api/settings/retention")
def set_retention(body: RetentionSettings, store: RequestStore = Depends(get_store)) -> dict:
    store.update_settings({"request_retention_days": body.days})
    return {"days": body.days}


# -- Stage 7: the pipeline preferences a household is actually likely to
#    want to tune (resolution floor, size range, language lists, category)
#    — see pipeline_settings.py for why the deeper scoring weights aren't
#    exposed here. Takes effect on the very next request; no restart, no
#    deploy — worker.py resolves these fresh from the store every run. --


@app.get("/api/settings/pipeline")
def get_pipeline_settings(store: RequestStore = Depends(get_store)) -> dict:
    return asdict(resolve_pipeline_settings(store))


@app.put("/api/settings/pipeline")
def set_pipeline_settings(body: PipelineSettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    patch = {
        "category": body.category,
        "min_resolution": body.min_resolution,
        "min_size_gb": body.min_size_gb,
        "max_size_gb": body.max_size_gb,
        "language_allowlist": body.language_allowlist,
        "language_blocklist": body.language_blocklist,
        "language_required": body.language_required,
    }
    # Validate the *effective* result of this patch, not just the two
    # fields in isolation — editing only min_size_gb while max_size_gb
    # reverts to its default (or vice versa) could otherwise silently
    # invert the range without either field looking wrong on its own.
    prospective = settings_from_raw({**store.get_settings(), **patch})
    if prospective.min_size_gb >= prospective.max_size_gb:
        raise HTTPException(
            status_code=422,
            detail=f"min_size_gb ({prospective.min_size_gb}) must be less than max_size_gb ({prospective.max_size_gb})",
        )
    store.update_settings(patch)
    return asdict(resolve_pipeline_settings(store))


# -- Stage 12.x: show-check interval and episode auto-recheck scheduling —
#    same "takes effect on the next wake-up, no restart" pattern as the
#    pipeline settings above. `episode_recheck_enabled` defaults to False
#    (opt-in): this is new, automatic, unattended behavior, including
#    auto-replacing an already-downloaded file on a quality upgrade. --


@app.get("/api/settings/tv")
def get_tv_settings(store: RequestStore = Depends(get_store)) -> dict:
    return asdict(resolve_tv_settings(store))


@app.put("/api/settings/tv")
def set_tv_settings(body: TVScheduleSettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    patch = {
        "show_check_interval_hours": body.show_check_interval_hours,
        "episode_recheck_enabled": body.episode_recheck_enabled,
        "episode_recheck_interval_hours": body.episode_recheck_interval_hours,
        "episode_recheck_max_attempts": body.episode_recheck_max_attempts,
        "episode_air_buffer_hours": body.episode_air_buffer_hours,
    }
    store.update_settings(patch)
    return asdict(resolve_tv_settings(store))


# -- Plex account linking (PIN sign-in). The resulting token is stored
#    server-side only — these routes never return it. See plex.py. --


@app.post("/api/plex/link")
async def start_plex_link(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    try:
        auth_url = await linker.start()
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"auth_url": auth_url}


@app.get("/api/plex/status")
def plex_status(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    return linker.status()


@app.post("/api/plex/unlink")
def unlink_plex(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    linker.unlink()
    return linker.status()


@app.get("/api/health")
def health(qbt: QBTClient = Depends(get_qbt)) -> dict:
    """Always 200 — the backend process being reachable at all is the
    caller's first signal. `qbittorrent: false` means the *dependency* is
    unreachable right now (network blip, qBittorrent restarting), not that
    this backend is broken, so it deliberately doesn't fail the request or
    double as a container-restart trigger — per "fail safe, not best
    guess," a flapping external dependency shouldn't take this app down
    with it."""
    return {"status": "ok", "qbittorrent": qbt.ping()}


@app.get("/api/storage")
def get_storage() -> dict:
    """Backs the always-visible storage indicator in the frontend's global
    chrome. Deliberately reads the filesystem directly (`shutil.disk_usage`
    on TV_LIBRARY_ROOT) rather than qBittorrent's own API: qBittorrent's
    `sync/maindata` only ever reports `free_space_on_disk`, never total
    capacity (confirmed live against the real instance), so there's no way
    to derive a used-percentage from qBittorrent alone. TV_LIBRARY_ROOT is
    the same underlying dataset qBittorrent itself downloads into (see
    media_organizer.py's translate_qbit_save_path comment), so this is
    still genuinely "the same disk qBittorrent is filling up" — just read
    from the one place that actually knows its total size. Movie and TV
    libraries currently share that one dataset too (see truenas/custom-
    app-compose.yaml's own note); if/when they're ever split onto separate
    physical disks this would need to report both, a named gap for later.

    Always 200, same "fail safe, not build-breaking" convention as
    /api/health — a workstation/test environment with no real mount at
    TV_LIBRARY_ROOT (or any other transient stat failure) degrades to
    `available: false` rather than an error toast on every single page
    load, since this backs a persistent global UI element, not a page a
    user navigated to on purpose."""
    try:
        usage = shutil.disk_usage(config.TV_LIBRARY_ROOT)
    except OSError:
        return {"available": False}
    used_percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
    return {
        "available": True,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": used_percent,
    }


# -- Stage 8: raw JSON view of failure-shaped jobs, gated the same way as
#    /api/admin/deploy below (visible, no real auth) — meant to be checked
#    with curl, not SSHed into; no frontend UI until it's actually needed
#    often enough to justify one (Stage 8's own open decision). --

_FAILURE_STATUSES = {"failed", "no qualifying results", "insufficient free space", "downloaded, not filed"}


@app.get("/api/admin/jobs")
def admin_jobs(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[RequestOut]:
    statuses = [status] if status else sorted(_FAILURE_STATUSES)
    rows = [r for s in statuses for r in store.list_requests(status=s)]
    rows.sort(key=lambda r: r.id, reverse=True)
    return [RequestOut.from_row(r) for r in rows]


@app.post("/api/admin/deploy")
def deploy() -> dict:
    """Runs exactly `git pull --ff-only` against the deployed-copy clone —
    see app/deploy.py. No parameters ever accepted. Gated only by the
    Settings panel's hidden long-press control on the frontend; this route
    itself has no auth, an accepted risk per the Stage 6 plan (blast radius
    is bounded since it only ever runs this one fixed command)."""
    try:
        return run_git_pull()
    except DeployError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
