"""FastAPI wrapper around Stages 1-2's library code, plus the SQLite job
store. Same-origin only (confirmed architecture) — no CORS middleware; the
frontend (Stage 4) reaches this only via nginx's reverse proxy on the same
origin. TMDB key and qBittorrent credentials never reach the browser: every
route here is either a thin TMDB proxy or reads/writes the local job store.

Frontend migration Part C: every route is protected by default, not
opt-in — `router`/`admin_router` below carry a blanket `Depends(...)`
rather than each route adding its own, specifically so a route added later
can't silently ship unauthenticated by a forgotten decorator. The small,
explicit allowlist that stays directly on `app` (unauthenticated) is
`/api/health`, `/api/auth/login/*`, and `/api/setup/*` (which has its own
token-based gate, not none at all — see require_setup_token)."""

import logging
import re
import secrets
import shutil
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import config, trailers
from app.db import RequestRow, RequestStore, SessionRow, ShowRow
from app.deploy import DeployError, run_git_pull
from app.logging_config import configure_logging
from app.pipeline_settings import (
    VALID_MIN_RESOLUTIONS,
    is_valid_min_resolution,
    resolve_pipeline_settings,
    settings_from_raw,
)
from app.plex import LoginSession, PlexClient, PlexError, PlexLinker, plex_library_lookup
from app.qbt import QBTClient
from app.resolve import resolve
from app.tmdb import TMDBClient, TMDBError, best_trailer_key, is_movie_coming_soon, is_tv_upcoming
from app.tv_resolve import resolve_show
from app.tv_settings import resolve_tv_settings
from app.worker import Worker

# Cache filenames are always trailers.cached_trailer_path()'s own
# "{media_type}-{tmdb_id}-{key}.mp4" shape — validated before ever touching
# the filesystem so a crafted filename can't path-traverse out of
# TRAILER_CACHE_DIR.
_TRAILER_FILENAME_RE = re.compile(r"^[a-z]+-\d+-[\w-]+\.mp4$")

configure_logging()
logger = logging.getLogger("app.api")

SESSION_COOKIE_NAME = "session_id"
# Sliding expiry, not absolute — every successful `require_session` check
# could in principle refresh it, but isn't wired up (yet) to do so; a
# session simply needs re-establishing via login after this long regardless
# of activity. 14 days is a starting default, easy to change later.
SESSION_TTL_DAYS = 14


def _new_session_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()


def _cookie_secure(store: RequestStore) -> bool:
    """Part G4 — the `Secure` flag genuinely requires HTTPS, which this
    app never terminates itself (a reverse proxy does, per Part G3); the
    admin's own `remote_access_enabled` toggle is what tells the backend
    that HTTPS is actually in front of it. `False` (LAN-only, plain HTTP)
    by default, same as `remote_access_enabled` itself."""
    return bool(store.get_settings().get("remote_access_enabled"))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = RequestStore(config.DB_PATH)
    app.state.store = store

    # TMDBClient no longer raises on an empty/missing key (see tmdb.py) —
    # the app must boot, and serve the first-run setup wizard's own
    # routes, even before a key exists anywhere. Resolved once, here, at
    # startup — see get_tmdb's own docstring for why this isn't
    # re-resolved per-request (a key saved through the wizard needs a
    # restart to take effect, a named/accepted gap, not silently solved).
    app.state.tmdb = TMDBClient(config.resolve_tmdb_api_key(store) or "")

    # qBittorrent's own client logs in eagerly at construction (see
    # qbt.py) — unlike TMDB, that can't be deferred without touching the
    # third-party client library it wraps. A fresh install with qBittorrent
    # not yet configured/reachable must still boot successfully (the setup
    # wizard is exactly what configures it), so a construction failure here
    # is caught rather than left to crash startup.
    qbt_config = config.resolve_qbt_config(store)
    try:
        app.state.qbt = QBTClient(qbt_config.host, qbt_config.port, qbt_config.username, qbt_config.password)
    except Exception as exc:  # pragma: no cover — exact exception type is qbittorrentapi's, not ours to depend on
        logger.warning("qBittorrent unreachable at startup (%s) — background worker not started this boot", exc)
        app.state.qbt = None

    app.state.worker = Worker(store, app.state.tmdb, app.state.qbt) if app.state.qbt else None
    app.state.login_session = LoginSession(store)
    app.state.plex_linker = PlexLinker(store)

    # Frontend migration Part G1: the whole point of the setup token is
    # that the admin finds it by checking this container's own logs —
    # generate (or recall the already-persisted one, see
    # get_or_create_setup_token's own docstring) it and print it clearly
    # right at startup, every boot until setup actually completes. Doing
    # this lazily (only whenever the first /api/setup/* request happened
    # to land) would mean there's nowhere the admin could ever actually
    # go read it from before making that first request.
    if not store.get_settings().get("plex_server_machine_id"):
        token = store.get_or_create_setup_token()
        logger.warning("=" * 60)
        logger.warning("First-run setup needed. Setup code: %s", token)
        logger.warning("Enter this in the setup wizard to continue.")
        logger.warning("=" * 60)

    if app.state.worker:
        await app.state.worker.start()
    else:
        # Named, accepted gap (frontend migration Part E): on a fresh
        # install, qBittorrent isn't reachable yet at this first boot (the
        # setup wizard is what configures it) — the worker, and any route
        # depending on get_worker/get_qbt, stay unavailable until the next
        # restart after that's fixed. Only reachable at all before
        # qBittorrent's ever been configured; nothing could have been
        # queued yet for the worker to act on regardless.
        logger.warning("worker not started: qBittorrent was not reachable at boot")
    try:
        yield
    finally:
        if app.state.worker:
            await app.state.worker.stop()
        store.close()


app = FastAPI(title="Meridian", lifespan=lifespan)

# Frontend migration Part G4 — rate limiting on the routes an internet
# attacker would actually script against once remote access is enabled:
# /api/auth/login/* (credential-guessing-shaped, even though "credential"
# here just means "does this Plex account have access") and /api/setup/*
# (the bootstrap race Part G1's LAN restriction already narrows, this is
# the second layer). In-memory, per-process — genuinely fine at household
# scale (one backend process, no multi-worker deployment), not meant to
# survive a restart. 20/minute is generous enough that no normal user
# interaction (including a slow multi-attempt login) ever brushes it,
# while still bounding a scripted attacker to a rate that isn't useful.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def get_store(request: Request) -> RequestStore:
    return request.app.state.store


def get_tmdb(request: Request) -> TMDBClient:
    """A plain accessor, not a re-resolving one — deliberately kept
    simple (frontend migration Part E) rather than rebuilding the client
    whenever the resolved TMDB key changes: this app's test suite injects
    fakes directly onto `app.state.tmdb`/`app.state.qbt` (see
    test_api.py's `client_and_deps` fixture), and a "recreate the real
    client on every call" version has no clean way to tell a genuine
    config change apart from "this is a fake with no `api_key`
    attribute" — tried, and it broke ~70 existing tests outright. Net
    effect: a TMDB key or qBittorrent connection saved through the setup
    wizard needs a restart to take effect, same accepted-gap shape as the
    project's existing "a changed requirements.txt needs a manual image
    recreate" — not solved here, but named rather than silently
    papered over."""
    return request.app.state.tmdb


def get_qbt(request: Request) -> QBTClient:
    """See get_tmdb's docstring immediately above — same reasoning,
    same accepted restart-needed gap."""
    return request.app.state.qbt


def get_worker(request: Request) -> Worker:
    worker = request.app.state.worker
    if worker is None:
        raise HTTPException(
            status_code=503, detail="background worker isn't running — qBittorrent wasn't reachable at startup"
        )
    return worker


def get_plex_linker(request: Request) -> PlexLinker:
    return request.app.state.plex_linker


def get_login_session(request: Request) -> LoginSession:
    return request.app.state.login_session


# ---------------------------------------------------------------------------
# Auth dependencies (frontend migration Part C3)
# ---------------------------------------------------------------------------


def require_session(request: Request, store: RequestStore = Depends(get_store)) -> SessionRow:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="not signed in")
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    return session


def require_admin(session: SessionRow = Depends(require_session)) -> SessionRow:
    if not session.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return session


def require_admin_or_setup_bootstrap(
    request: Request, store: RequestStore = Depends(get_store)
) -> SessionRow | None:
    """Gate for POST /api/plex/link, GET /api/plex/status, and the new
    multi-server routes: unauthenticated (but setup-token-checked) while
    setup isn't complete yet — there's no admin session possible before
    this completes, since the admin's identity *is* whoever completes it
    — otherwise admin-only. See require_setup_token's docstring for why
    the token check duplicates a few lines rather than composing with it.

    Keyed on `plex_server_machine_id`, not `plex_token` — `plex_token`
    is set as soon as the PIN sign-in itself succeeds, which is *before*
    the account has actually picked which owned server to use (a
    separate step, GET /api/plex/servers + PUT /api/plex/server). Keying
    on `plex_token` would flip this into "admin required" the instant
    sign-in succeeds but before server selection has run — including on
    the GET /api/plex/servers call the wizard needs to show the picker in
    the first place, a genuine deadlock caught while building the setup
    wizard's frontend. `plex_server_machine_id` is only ever set once
    PUT /api/plex/server actually finishes, which is what completes setup."""
    if not store.get_settings().get("plex_server_machine_id"):
        expected = store.get_or_create_setup_token()
        if request.headers.get("X-Setup-Token") != expected:
            raise HTTPException(status_code=401, detail="missing or incorrect setup token")
        return None
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="not signed in")
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    if not session.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return session


def require_setup_token(request: Request, store: RequestStore = Depends(get_store)) -> None:
    """Gates /api/setup/* until initial setup completes (frontend
    migration Part G1) — closes the race where, on an internet-reachable
    instance, a stranger who reaches an unset-up install first could
    claim the admin slot before the real admin does. In production this
    sits behind nginx's own LAN-only restriction on the same path
    (Part G1's primary control); this is the defense-in-depth second
    layer that holds even without nginx in front (e.g. this test suite,
    or `npm run dev` against a bare uvicorn). Keyed on
    `plex_server_machine_id`, not `plex_token` — see
    require_admin_or_setup_bootstrap's docstring for why."""
    if store.get_settings().get("plex_server_machine_id"):
        raise HTTPException(status_code=410, detail="setup already completed")
    expected = store.get_or_create_setup_token()
    if request.headers.get("X-Setup-Token") != expected:
        raise HTTPException(status_code=401, detail="missing or incorrect setup token")


# Default-deny: every route registered on `router` requires a valid
# signed-in session, `admin_router` additionally requires that session to
# be the admin's. See this module's own docstring for the small, explicit
# allowlist that deliberately stays off both.
router = APIRouter(dependencies=[Depends(require_session)])
admin_router = APIRouter(dependencies=[Depends(require_admin)])


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
    # Per-request floor override — the detail page's "Download 4K"/
    # "Download 1080p" shortcuts. None = use the global pipeline setting.
    min_resolution: str | None = None
    # frontend migration Part K1/K2: set only from the "Already on Plex"
    # confirmation modal. "overwrite" is rejected below (create_request)
    # unless this tmdb_id has a request this app itself organized on
    # record — never offered against a file only the fuzzy on_plex
    # title/year match found.
    redownload_mode: str | None = None

    @field_validator("min_resolution")
    @classmethod
    def _known_resolution(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_min_resolution(v):
            raise ValueError(f"min_resolution must be one of {VALID_MIN_RESOLUTIONS}")
        return v

    @field_validator("redownload_mode")
    @classmethod
    def _known_redownload_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("upgrade", "overwrite"):
            raise ValueError("redownload_mode must be 'upgrade' or 'overwrite'")
        return v


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
    # Frontend migration Part C2 — who asked for this (None for
    # worker-created rows, e.g. a subscribed show's automatic catch-up).
    requested_by_plex_id: str | None = None
    requested_by_username: str | None = None
    # Frontend migration Part K1/K3 — "upgrade"/"overwrite"/None, drives
    # the queue's "Redownload" tag.
    redownload_mode: str | None = None
    # Frontend migration Part J1/J2 — poster art and live download
    # progress for the Requests queue, both denormalized/refreshed the
    # same way redownload_mode's neighbors above already are.
    poster_path: str | None = None
    download_progress: float | None = None

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
    # Frontend migration Part J4 — per-request floor override, same
    # convention as CreateRequest.min_resolution (movies), now also
    # available for a season/series bulk download.
    min_resolution: str | None = None
    # Frontend migration Part K3 — TV gets the same redownload treatment
    # as movies. Accepted and stored for a pack request, but the actual
    # overwrite-deletion mechanism (Part K2) is movie-only for now; an
    # "overwrite" pack request behaves like "upgrade" until that's built
    # out for packs too — a named, not-yet-solved gap in the same style
    # as this project's others, not silently pretended to be complete.
    redownload_mode: str | None = None

    @field_validator("scope")
    @classmethod
    def _known_scope(cls, v: str) -> str:
        if v not in ("season", "series"):
            raise ValueError("scope must be 'season' or 'series'")
        return v

    @field_validator("min_resolution")
    @classmethod
    def _known_resolution(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_min_resolution(v):
            raise ValueError(f"min_resolution must be one of {VALID_MIN_RESOLUTIONS}")
        return v

    @field_validator("redownload_mode")
    @classmethod
    def _known_redownload_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("upgrade", "overwrite"):
            raise ValueError("redownload_mode must be 'upgrade' or 'overwrite'")
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
    # Frontend migration Part J1 — see RequestRow's own comment.
    poster_path: str | None = None
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


@router.post("/api/search")
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


@router.get("/api/discover/popular")
def discover_popular(page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    # Digital-availability filtered: Coming Soon is the dedicated tab for
    # theatrical-only titles, so Discover shouldn't also surface them.
    try:
        data = tmdb.get_available_popular(page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@router.get("/api/discover/trending")
def discover_trending(
    time_window: str = "week", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_available_trending(time_window=time_window, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@router.get("/api/discover/providers")
def discover_providers(region: str = "US", tmdb: TMDBClient = Depends(get_tmdb)) -> list[dict]:
    try:
        data = tmdb.get_watch_providers(region=region)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data.get("results", [])


@router.get("/api/discover/providers/{provider_id}")
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


@router.get("/api/discover/genre/{genre_id}")
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


@router.get("/api/discover/coming-soon")
def discover_coming_soon(
    region: str = "US", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_coming_soon(region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


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
    is_coming_soon = is_movie_coming_soon(movie, release_dates, region="US")
    return {
        **movie,
        "on_plex": _on_plex_for(movie.get("title") or "", year, "movie", store),
        "is_coming_soon": is_coming_soon,
        # Frontend migration Part K2 — true only when this app has a
        # confirmed record of having organized a file for this title
        # itself, never derived from the same fuzzy on_plex title/year
        # match above. Drives whether "Overwrite existing" is even
        # offered in the redownload confirmation modal.
        "on_plex_tracked": store.get_latest_organized_request(tmdb_id, ("movie",)) is not None,
    }


@router.get("/api/movies/{tmdb_id}/trailer")
def get_movie_trailer(tmdb_id: int, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """Backs the home hero carousel's background video — a separate call
    from get_movie_detail rather than another append_to_response, since
    this is only ever fetched for the couple of hero slides that actually
    need a trailer, not every movie the frontend touches. `url: null`
    (never a 404) when nothing suitable is on file or the download fails —
    a title with no trailer is a normal, expected case, not an error the
    caller needs to handle specially; it just falls back to a plain
    poster/backdrop. Downloads and serves the clip from our own cache
    (trailers.py) rather than embedding YouTube's player — see that
    module's docstring for why."""
    try:
        videos = tmdb.get_movie_videos(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    key = best_trailer_key(videos)
    if key is None:
        return {"url": None}
    path = trailers.ensure_downloaded("movie", tmdb_id, key)
    return {"url": f"/api/trailers/{path.name}" if path else None}


# -- Stage 14: TV browse surface — the show equivalent of the movie routes
#    above. Same thin-pass-through/on_plex-annotation/key-never-reaches-
#    the-browser rules. --


@router.get("/api/tv/discover/popular")
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


@router.get("/api/tv/discover/trending")
def tv_discover_trending(
    time_window: str = "week", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_available_tv_trending(time_window=time_window, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/tv/discover/providers/{provider_id}")
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


@router.get("/api/tv/discover/genre/{genre_id}")
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


@router.get("/api/tv/discover/coming-soon")
def tv_discover_coming_soon(
    region: str = "US", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_tv_coming_soon(region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/tv/{tmdb_id}")
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
        # Frontend migration Part K3 — TV parity with the movie route
        # above. A show's organized history is episode/pack rows, never
        # a single fixed media_type the way a movie's always is.
        "on_plex_tracked": store.get_latest_organized_request(tmdb_id, ("episode", "pack")) is not None,
    }


@router.get("/api/tv/{tmdb_id}/trailer")
def get_tv_trailer(tmdb_id: int, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """The TV equivalent of get_movie_trailer above — same reasoning."""
    try:
        videos = tmdb.get_tv_videos(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    key = best_trailer_key(videos)
    if key is None:
        return {"url": None}
    path = trailers.ensure_downloaded("tv", tmdb_id, key)
    return {"url": f"/api/trailers/{path.name}" if path else None}


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
    return FileResponse(path, media_type="video/mp4")


@router.get("/api/person/{person_id}")
def get_person_detail(person_id: int, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """A cast member's filmography — backs the "click an actor" detail
    page. Only what the frontend actually needs: name/photo plus a
    deduped, newest-first credits list (each item already carrying its
    own media_type, so the frontend's existing posterCard() works on it
    unchanged, same as any other mixed movie/TV list in this app).

    Deduped on (id, media_type) — TMDB's combined_credits can list the
    same title more than once for a recurring/guest role across an
    actor's episodic appearances, which would otherwise show as a
    duplicate poster."""
    try:
        person = tmdb.get_person(person_id)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"person_id {person_id} not found") from exc
    cast = person.get("combined_credits", {}).get("cast", [])
    seen: set[tuple] = set()
    credits = []
    for c in sorted(cast, key=lambda c: c.get("release_date") or c.get("first_air_date") or "", reverse=True):
        key = (c.get("id"), c.get("media_type"))
        if key in seen:
            continue
        seen.add(key)
        credits.append(c)
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "profile_path": person.get("profile_path"),
        "known_for_department": person.get("known_for_department"),
        "credits": credits,
    }


@router.post("/api/tv/search")
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


@router.post("/api/requests", status_code=201)
def create_request(
    body: CreateRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
    session: SessionRow = Depends(require_session),
) -> RequestOut:
    try:
        identity = resolve(body.tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {body.tmdb_id} not found") from exc

    if body.redownload_mode == "overwrite" and store.get_latest_organized_request(body.tmdb_id, ("movie",)) is None:
        # Defense in depth — never trust the frontend's button state
        # alone. "Overwrite" is only ever offered against a file this
        # app has a confirmed record of organizing itself, never a guess
        # from the same fuzzy on_plex title/year match.
        raise HTTPException(
            status_code=400, detail="nothing on record for this title that this app organized itself"
        )

    row = store.create_request(
        tmdb_id=body.tmdb_id,
        title=identity.title,
        release_year=identity.release_year,
        query=body.query,
        min_resolution=body.min_resolution,
        requested_by_plex_id=session.plex_user_id,
        requested_by_username=session.username,
        redownload_mode=body.redownload_mode,
        poster_path=identity.poster_path,
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


@router.get("/api/requests")
def list_requests(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[RequestOut]:
    return [RequestOut.from_row(r) for r in store.list_requests(status=status)]


@router.get("/api/requests/{request_id}")
def get_request(request_id: int, store: RequestStore = Depends(get_store)) -> RequestOut:
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    return RequestOut.from_row(row)


@router.post("/api/requests/clear")
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


def _cancel_active_torrent(row: RequestRow, store: RequestStore, qbt: QBTClient) -> str:
    """Shared by `cancel` and `reject` for a "downloading"/"complete" row:
    validates status/torrent-on-record/still-in-qBittorrent, deletes the
    torrent and its files, marks the request "cancelled", and returns the
    torrent hash (the one extra thing `reject` needs on top of everything
    `cancel` already does)."""
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
    store.update_status(row.id, "cancelled")
    return torrent_hash


@router.post("/api/requests/{request_id}/cancel")
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

    _cancel_active_torrent(row, store, qbt)
    logger.info("request %d (%s) downloading/complete -> cancelled (via API)", request_id, row.title)
    return RequestOut.from_row(store.get_request(request_id))


@router.post("/api/requests/{request_id}/reject")
def reject_request(
    request_id: int, store: RequestStore = Depends(get_store), qbt: QBTClient = Depends(get_qbt)
) -> RequestOut:
    """Stage 15: like `cancel`, but for a torrent that turned out to be
    genuinely defective despite looking like the best candidate on paper —
    a bad encode, audio sync drift, wrong cut, anything the search/scoring
    pipeline's filename/seeder-based signals could never have detected up
    front (confirmed live: a well-seeded, top-scored 2160p "Mutiny" release
    with progressive audio sync drift, 2026-09-14). Deletes the torrent and
    its files the same way `cancel` does, but additionally blacklists this
    exact torrent hash against the request's tmdb_id
    (`RequestStore.add_rejected_torrent`), so a fresh search for the same
    movie/show excludes it and falls through to the next-best-scored
    candidate instead of re-selecting the same defective release.

    Only valid for a "downloading"/"complete" row — a "queued" request has
    no torrent on record yet to reject."""
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    if row.status == "queued":
        raise HTTPException(status_code=409, detail="a queued request has no torrent yet to reject")

    torrent_hash = _cancel_active_torrent(row, store, qbt)
    store.add_rejected_torrent(row.tmdb_id, torrent_hash)
    logger.info(
        "request %d (%s) downloading/complete -> cancelled (rejected, torrent hash blacklisted)",
        request_id,
        row.title,
    )
    return RequestOut.from_row(store.get_request(request_id))


# -- Stage 12: standing show subscriptions. POST creates the subscription
#    and immediately runs a *full* backfill catch-up (Stage 14.x — every
#    season from 1 through the current latest, not just the latest), so
#    subscribing to a show with nothing downloaded at all grabs everything
#    already aired, not only its newest season. Every subsequent scheduled
#    recheck (worker.py's `_check_all_watching_shows`) keeps checking only
#    the latest season, same as always — this full sweep only ever runs
#    once, right here, at the moment of subscribing. --


@router.post("/api/shows", status_code=201)
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

    row = store.create_show(tmdb_id=body.tmdb_id, title=identity.title, poster_path=identity.poster_path)
    worker.check_show(row, full_backfill=True)
    return _show_out(store, store.get_show(row.id))


@router.get("/api/shows")
def list_shows(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[ShowOut]:
    return [_show_out(store, r) for r in store.list_shows(status=status)]


@router.get("/api/shows/{show_id}")
def get_show(show_id: int, store: RequestStore = Depends(get_store)) -> ShowOut:
    row = store.get_show(show_id)
    if row is None:
        raise HTTPException(status_code=404, detail="show not found")
    return _show_out(store, row)


@router.post("/api/shows/{show_id}/pause")
def pause_show(show_id: int, store: RequestStore = Depends(get_store)) -> ShowOut:
    if store.get_show(show_id) is None:
        raise HTTPException(status_code=404, detail="show not found")
    store.update_show_status(show_id, "paused")
    return _show_out(store, store.get_show(show_id))


@router.post("/api/shows/{show_id}/resume")
def resume_show(show_id: int, store: RequestStore = Depends(get_store)) -> ShowOut:
    if store.get_show(show_id) is None:
        raise HTTPException(status_code=404, detail="show not found")
    store.update_show_status(show_id, "watching")
    return _show_out(store, store.get_show(show_id))


@router.delete("/api/shows/{show_id}")
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


@router.post("/api/tv/{tmdb_id}/bulk-download", status_code=201)
def bulk_download_show(
    tmdb_id: int,
    body: BulkDownloadRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
    session: SessionRow = Depends(require_session),
) -> RequestOut:
    show = store.get_show_by_tmdb_id(tmdb_id)
    if show is None:
        try:
            identity = resolve_show(tmdb_id, tmdb)
        except TMDBError as exc:
            raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
        show = store.create_show(
            tmdb_id=tmdb_id, title=identity.title, status="paused", poster_path=identity.poster_path
        )

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
        requested_by_plex_id=session.plex_user_id,
        requested_by_username=session.username,
        min_resolution=body.min_resolution,
        # No on_plex_tracked/400 gate here unlike the movie route — TV's
        # actual overwrite-deletion mechanism isn't built yet (see
        # BulkDownloadRequest's own docstring), so there's nothing yet
        # for "overwrite" to unsafely bypass; it's accepted and stored,
        # but behaves like "upgrade" until that's built out.
        redownload_mode=body.redownload_mode,
        poster_path=show.poster_path,
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


@admin_router.get("/api/settings/retention")
def get_retention(store: RequestStore = Depends(get_store)) -> dict:
    return {"days": store.get_settings().get("request_retention_days")}


@admin_router.put("/api/settings/retention")
def set_retention(body: RetentionSettings, store: RequestStore = Depends(get_store)) -> dict:
    store.update_settings({"request_retention_days": body.days})
    return {"days": body.days}


# -- Stage 7: the pipeline preferences a household is actually likely to
#    want to tune (resolution floor, size range, language lists, category)
#    — see pipeline_settings.py for why the deeper scoring weights aren't
#    exposed here. Takes effect on the very next request; no restart, no
#    deploy — worker.py resolves these fresh from the store every run. --


@admin_router.get("/api/settings/pipeline")
def get_pipeline_settings(store: RequestStore = Depends(get_store)) -> dict:
    return asdict(resolve_pipeline_settings(store))


@admin_router.put("/api/settings/pipeline")
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


@admin_router.get("/api/settings/tv")
def get_tv_settings(store: RequestStore = Depends(get_store)) -> dict:
    return asdict(resolve_tv_settings(store))


@admin_router.put("/api/settings/tv")
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


# -- Plex account linking (admin server-linking, PIN sign-in). The
#    resulting token is stored server-side only — these routes never
#    return it. See plex.py. Gated by require_admin_or_setup_bootstrap
#    (frontend migration Part C3): unauthenticated-but-token-checked while
#    no server is linked yet (there's no admin to authenticate as before
#    this completes — the admin's identity *is* whoever completes it),
#    admin-only afterward (re-linking/switching is an ongoing admin
#    action, not a bootstrap one). --


@app.post("/api/plex/link", dependencies=[Depends(require_admin_or_setup_bootstrap)])
async def start_plex_link(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    try:
        auth_url = await linker.start()
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"auth_url": auth_url}


@app.get("/api/plex/status", dependencies=[Depends(require_admin_or_setup_bootstrap)])
def plex_status(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    return linker.status()


@app.post("/api/plex/unlink", dependencies=[Depends(require_admin)])
def unlink_plex(
    request: Request, linker: PlexLinker = Depends(get_plex_linker), store: RequestStore = Depends(get_store)
) -> dict:
    linker.unlink()
    store.record_auth_event("plex_unlinked", ip_address=_client_ip(request))
    return linker.status()


@app.get("/api/plex/servers", dependencies=[Depends(require_admin_or_setup_bootstrap)])
def list_plex_servers(store: RequestStore = Depends(get_store)) -> list[dict]:
    """Every Plex server this account *owns*, using the already-persisted
    account-level token — no fresh PIN sign-in needed (frontend migration
    Part C1). Backs both the setup wizard's server picker (an account can
    own more than one server) and Settings' "Switch Server" action."""
    settings = store.get_settings()
    token = settings.get("plex_token")
    client_id = settings.get("plex_client_id")
    if not token or not client_id:
        raise HTTPException(status_code=409, detail="no Plex account linked yet — sign in first")
    client = PlexClient(client_id)
    try:
        resources = client.list_resources(token)
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        {"name": r["name"], "machine_identifier": r["machine_identifier"]} for r in resources if r["owned"]
    ]


class SelectPlexServerRequest(BaseModel):
    machine_identifier: str = Field(min_length=1)


@app.put("/api/plex/server")
def select_plex_server(
    request: Request,
    body: SelectPlexServerRequest,
    response: Response,
    store: RequestStore = Depends(get_store),
    session: SessionRow | None = Depends(require_admin_or_setup_bootstrap),
) -> dict:
    """Finalizes the setup wizard's server picker, or (post-setup) an
    admin switching to a different owned server. Either way, re-resolves
    fresh connection details for the chosen server (a connection URL can
    change) rather than trusting whatever list_plex_servers last returned.

    `session` is None exactly when this call is what's finishing bootstrap
    (require_admin_or_setup_bootstrap's own bootstrap branch) — in that
    case this is also the moment the just-linked account should actually
    become signed in, not just "the server now knows who the admin is."
    Without this, completing setup would leave the admin having to run a
    *second*, separate PIN sign-in immediately after the one they just
    did to link the server in the first place, which is confusing on top
    of "why did that already work."""
    settings = store.get_settings()
    token = settings.get("plex_token")
    client_id = settings.get("plex_client_id")
    if not token or not client_id:
        raise HTTPException(status_code=409, detail="no Plex account linked yet — sign in first")
    client = PlexClient(client_id)
    try:
        resources = client.list_resources(token)
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    match = next(
        (r for r in resources if r["owned"] and r["machine_identifier"] == body.machine_identifier), None
    )
    if match is None:
        raise HTTPException(status_code=404, detail="server not found among this account's owned servers")
    store.update_settings(
        {
            "plex_server_url": match["url"],
            "plex_server_token": match["token"],
            "plex_server_name": match["name"],
            "plex_server_machine_id": match["machine_identifier"],
        }
    )
    # Switching servers changes who's authorized — every non-admin
    # session's access grant was checked against the *old* server and
    # must be re-validated via a fresh login against the new one.
    store.delete_non_admin_sessions()
    store.record_auth_event(
        "plex_server_selected",
        ip_address=_client_ip(request),
        detail=f"linked to {match['name']}",
    )

    if session is None:
        try:
            identity = client.get_account_identity(token)
        except Exception:  # fail safe: setup itself already succeeded above regardless of this
            identity = None
        if identity and identity.get("id"):
            plex_user_id = str(identity["id"])
            user = store.upsert_user(plex_user_id, identity.get("username"), True)
            session_id = secrets.token_urlsafe(32)
            store.create_session(session_id, user.plex_user_id, user.username, True, _new_session_expiry())
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=session_id,
                httponly=True,
                samesite="strict",
                secure=_cookie_secure(store),
                max_age=SESSION_TTL_DAYS * 24 * 3600,
                path="/",
            )
    return {"server_name": match["name"]}


# ---------------------------------------------------------------------------
# End-user login (frontend migration Part C3) — Plex PIN sign-in, same
# mechanism as admin server-linking above, different purpose: authenticates
# *one person* against the already-linked server rather than linking the
# server itself. See plex.py's LoginSession.
# ---------------------------------------------------------------------------


@app.post("/api/auth/login/start")
@limiter.limit("20/minute")
async def start_login(request: Request, login: LoginSession = Depends(get_login_session)) -> dict:
    try:
        auth_url = await login.start()
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"auth_url": auth_url}


@app.get("/api/auth/login/status")
@limiter.limit("20/minute")
def login_status(
    request: Request,
    response: Response,
    login: LoginSession = Depends(get_login_session),
    store: RequestStore = Depends(get_store),
) -> dict:
    status = login.status()
    result = status["result"]
    user = None
    if result:
        user = store.upsert_user(result["plex_user_id"], result["username"], result["is_admin"])
        session_id = secrets.token_urlsafe(32)
        store.create_session(session_id, user.plex_user_id, user.username, user.is_admin, _new_session_expiry())
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            httponly=True,
            samesite="strict",
            secure=_cookie_secure(store),
            max_age=SESSION_TTL_DAYS * 24 * 3600,
            path="/",
        )
        store.record_auth_event(
            "login_success",
            plex_user_id=user.plex_user_id,
            username=user.username,
            ip_address=_client_ip(request),
            detail="admin" if user.is_admin else "user",
        )
    elif status["error"]:
        store.record_auth_event(
            "login_failure", ip_address=_client_ip(request), detail=status["error"]
        )
    return {
        "pending": status["pending"],
        "authenticated": bool(result),
        "username": user.username if user else None,
        "is_admin": user.is_admin if user else None,
        "has_seen_tutorial": user.has_seen_tutorial if user else None,
        "error": status["error"],
    }


@router.get("/api/auth/session")
def get_current_session(store: RequestStore = Depends(get_store), session: SessionRow = Depends(require_session)) -> dict:
    user = store.get_user(session.plex_user_id)
    return {
        "username": session.username,
        "is_admin": session.is_admin,
        "has_seen_tutorial": user.has_seen_tutorial if user else False,
    }


@app.post("/api/auth/logout")
def logout(response: Response, request: Request, store: RequestStore = Depends(get_store)) -> dict:
    """No require_session — logging out an already-expired/invalid
    session should still succeed and clear the cookie, not 401."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        store.delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"logged_out": True}


@router.put("/api/auth/tutorial-seen")
def mark_tutorial_seen(store: RequestStore = Depends(get_store), session: SessionRow = Depends(require_session)) -> dict:
    store.mark_tutorial_seen(session.plex_user_id)
    return {"has_seen_tutorial": True}


# ---------------------------------------------------------------------------
# First-run setup wizard (frontend migration Part E) — TMDB key and
# qBittorrent connection, collected through the UI so a non-technical
# homelab install never needs to hand-edit .env. An env var always wins
# when set (existing installs, including this app's own NAS deploy, need
# zero changes); otherwise the value lives in the settings table, editable
# here during bootstrap. Every mutating route requires require_setup_token
# and 410s once setup is complete — ongoing post-setup editing is a
# separate, always-admin-gated surface (Settings' Connections panel, a
# later step of the migration, not built yet).
# ---------------------------------------------------------------------------


@app.get("/api/setup/status")
def setup_status(store: RequestStore = Depends(get_store)) -> dict:
    """No auth at all, deliberately — this is what the frontend calls
    *before* deciding whether to show the setup wizard or the login
    screen, so it can't itself be gated by either. Reveals only
    configuredness, never a secret value."""
    settings = store.get_settings()
    qbt_source = config.qbt_config_source(store)
    qbt_configured = qbt_source == "env" or bool(settings.get("qbt_host"))
    # Two distinct signals, not one — see require_admin_or_setup_bootstrap's
    # docstring: `plex_token` is set as soon as PIN sign-in succeeds (the
    # wizard uses `plex_account_linked` to know it can move on to showing
    # the server picker), but `setup_complete`/every auth gate elsewhere
    # keys specifically on a server having actually been *selected*
    # (`plex_server_machine_id`) — the step that comes after, and the one
    # that genuinely finishes bootstrap.
    plex_account_linked = bool(settings.get("plex_token"))
    setup_complete = bool(settings.get("plex_server_machine_id"))
    return {
        "tmdb_configured": bool(config.resolve_tmdb_api_key(store)),
        "tmdb_source": config.tmdb_api_key_source(store),
        "qbt_configured": qbt_configured,
        "qbt_source": qbt_source,
        "plex_account_linked": plex_account_linked,
        "plex_linked": setup_complete,
        "setup_complete": setup_complete,
    }


class SetupTmdbRequest(BaseModel):
    api_key: str = Field(min_length=1)


def _update_tmdb_key(body: SetupTmdbRequest, store: RequestStore) -> dict:
    """Saves, but doesn't immediately apply — get_tmdb's own docstring in
    this module explains why: it's resolved once at startup, not
    per-request. `restart_required: true` is what the caller's UI uses to
    tell the admin so, rather than implying this is live right away.
    Shared by the one-time setup route and Settings' ongoing Connections
    panel below — identical business rule, only the auth gate differs
    (setup token pre-bootstrap, admin session after), so each stays its
    own thin route rather than duplicating this logic twice."""
    if config.tmdb_api_key_source(store) == "env":
        raise HTTPException(status_code=409, detail="TMDB API key is already configured via environment variable")
    store.update_settings({"tmdb_api_key": body.api_key})
    return {"tmdb_configured": True, "tmdb_source": "db", "restart_required": True}


@app.put("/api/setup/tmdb", dependencies=[Depends(require_setup_token)])
@limiter.limit("20/minute")
def setup_tmdb(request: Request, body: SetupTmdbRequest, store: RequestStore = Depends(get_store)) -> dict:
    return _update_tmdb_key(body, store)


class SetupQbtRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(gt=0, lt=65536)
    username: str = ""
    password: str = ""


def _test_qbt_connection(body: SetupQbtRequest) -> dict:
    try:
        client = QBTClient(body.host, body.port, body.username, body.password)
    except Exception as exc:
        return {"reachable": False, "detail": str(exc)}
    return {"reachable": client.ping()}


@app.post("/api/setup/qbittorrent/test", dependencies=[Depends(require_setup_token)])
@limiter.limit("20/minute")
def setup_qbt_test(request: Request, body: SetupQbtRequest) -> dict:
    return _test_qbt_connection(body)


def _update_qbt_connection(body: SetupQbtRequest, store: RequestStore) -> dict:
    """Same "saved, not immediately applied" note as _update_tmdb_key
    above, and the same shared-helper reasoning."""
    if config.qbt_config_source(store) == "env":
        raise HTTPException(
            status_code=409, detail="qBittorrent connection is already configured via environment variables"
        )
    store.update_settings(
        {"qbt_host": body.host, "qbt_port": body.port, "qbt_username": body.username, "qbt_password": body.password}
    )
    return {"qbt_configured": True, "qbt_source": "db", "restart_required": True}


@app.put("/api/setup/qbittorrent", dependencies=[Depends(require_setup_token)])
@limiter.limit("20/minute")
def setup_qbt(request: Request, body: SetupQbtRequest, store: RequestStore = Depends(get_store)) -> dict:
    return _update_qbt_connection(body, store)


# -- Connections (frontend migration Part I) — Settings' ongoing,
#    always-admin-gated equivalent of the setup routes just above (which
#    410 permanently once setup completes, Part G1) — reuses their exact
#    same business-rule helpers, only the auth gate differs. --


@admin_router.put("/api/settings/tmdb")
def update_tmdb_settings(request: Request, body: SetupTmdbRequest, store: RequestStore = Depends(get_store)) -> dict:
    result = _update_tmdb_key(body, store)
    store.record_auth_event("tmdb_key_changed", ip_address=_client_ip(request))
    return result


@admin_router.post("/api/settings/qbittorrent/test")
def test_qbt_settings(body: SetupQbtRequest) -> dict:
    return _test_qbt_connection(body)


@admin_router.put("/api/settings/qbittorrent")
def update_qbt_settings(request: Request, body: SetupQbtRequest, store: RequestStore = Depends(get_store)) -> dict:
    result = _update_qbt_connection(body, store)
    store.record_auth_event("qbt_connection_changed", ip_address=_client_ip(request))
    return result


# -- Remote access (frontend migration Part G2) — informational/config,
#    not network automation (this app can't itself open a router port):
#    displays the configured public URL back to the admin, and drives
#    _cookie_secure() above. Defaults to False/None, same "opt-in,
#    admin-only, after the fact" principle Part G1's LAN-only setup
#    restriction exists to protect in the first place — an admin can only
#    ever reach this toggle once they're already authenticated, which
#    itself required completing setup from the LAN. --


class RemoteAccessSettings(BaseModel):
    remote_access_enabled: bool
    public_domain: str | None = None


class RemoteAccessIn(BaseModel):
    remote_access_enabled: bool
    public_domain: str | None = None

    @field_validator("public_domain")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


@admin_router.get("/api/settings/remote-access")
def get_remote_access_settings(store: RequestStore = Depends(get_store)) -> RemoteAccessSettings:
    settings = store.get_settings()
    return RemoteAccessSettings(
        remote_access_enabled=bool(settings.get("remote_access_enabled")),
        public_domain=settings.get("public_domain"),
    )


@admin_router.put("/api/settings/remote-access")
def update_remote_access_settings(
    request: Request, body: RemoteAccessIn, store: RequestStore = Depends(get_store)
) -> RemoteAccessSettings:
    store.update_settings(
        {"remote_access_enabled": body.remote_access_enabled, "public_domain": body.public_domain}
    )
    store.record_auth_event(
        "remote_access_toggled",
        ip_address=_client_ip(request),
        detail=("enabled" if body.remote_access_enabled else "disabled")
        + (f" ({body.public_domain})" if body.public_domain else ""),
    )
    return RemoteAccessSettings(remote_access_enabled=body.remote_access_enabled, public_domain=body.public_domain)


@app.get("/api/health")
def health(request: Request) -> dict:
    """Always 200 — the backend process being reachable at all is the
    caller's first signal. `qbittorrent: false` means the *dependency* is
    unreachable right now (network blip, qBittorrent restarting, or
    (frontend migration Part E) not yet configured/reachable on a fresh
    install, in which case `app.state.qbt` is None — see the lifespan's
    own handling), not that this backend is broken, so it deliberately
    doesn't fail the request or double as a container-restart trigger —
    per "fail safe, not best guess," a flapping external dependency
    shouldn't take this app down with it."""
    qbt = get_qbt(request)
    return {"status": "ok", "qbittorrent": bool(qbt and qbt.ping())}


@router.get("/api/storage")
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


# -- Stage 8: raw JSON view of failure-shaped jobs. Admin-only (frontend
#    migration Part C3), same as /api/admin/deploy below — meant to be
#    checked with curl, not SSHed into; no frontend UI until it's actually
#    needed often enough to justify one (Stage 8's own open decision). --

_FAILURE_STATUSES = {"failed", "no qualifying results", "insufficient free space", "downloaded, not filed"}


@admin_router.get("/api/admin/jobs")
def admin_jobs(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[RequestOut]:
    statuses = [status] if status else sorted(_FAILURE_STATUSES)
    rows = [r for s in statuses for r in store.list_requests(status=s)]
    rows.sort(key=lambda r: r.id, reverse=True)
    return [RequestOut.from_row(r) for r in rows]


# -- Frontend migration Part D: Settings' Activity Dashboard — every
#    request (not just the failure-shaped subset /api/admin/jobs above
#    covers), newest first, with who-asked-for-it attribution and simple
#    per-user aggregate counts. --


class RequesterStat(BaseModel):
    plex_user_id: str
    username: str | None
    total_requests: int
    requests_this_month: int


class ActivityOut(BaseModel):
    requests: list[RequestOut]
    total: int
    user_stats: list[RequesterStat]


@admin_router.get("/api/admin/activity")
def admin_activity(limit: int = 50, offset: int = 0, store: RequestStore = Depends(get_store)) -> ActivityOut:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    rows = store.list_requests_page(limit=limit, offset=offset)
    return ActivityOut(
        requests=[RequestOut.from_row(r) for r in rows],
        total=store.count_requests(),
        user_stats=[RequesterStat(**s) for s in store.get_requester_stats()],
    )


# -- Frontend migration Part G4: the audit log an admin can actually
#    check once this instance is internet-facing, and the "a device was
#    lost" panic button. --


class AuditEventOut(BaseModel):
    id: int
    event_type: str
    plex_user_id: str | None
    username: str | None
    ip_address: str | None
    detail: str | None
    created_at: str


class AuditLogOut(BaseModel):
    events: list[AuditEventOut]
    total: int


@admin_router.get("/api/admin/audit-log")
def get_audit_log(limit: int = 50, offset: int = 0, store: RequestStore = Depends(get_store)) -> AuditLogOut:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    events = store.list_auth_events(limit=limit, offset=offset)
    return AuditLogOut(events=[AuditEventOut(**e) for e in events], total=store.count_auth_events())


@admin_router.post("/api/admin/revoke-sessions")
def revoke_all_sessions(request: Request, store: RequestStore = Depends(get_store)) -> dict:
    """For if a device is ever lost — literally every signed-in session,
    including the one making this call (see delete_all_sessions's own
    docstring). The frontend's next authenticated request 401s and falls
    back to the login screen, same as any other expired session."""
    count = store.delete_all_sessions()
    store.record_auth_event("sessions_revoked", ip_address=_client_ip(request), detail=f"{count} session(s)")
    return {"revoked": count}


@admin_router.post("/api/admin/deploy")
def deploy(request: Request, store: RequestStore = Depends(get_store)) -> dict:
    """Runs exactly `git pull --ff-only` against the deployed-copy clone —
    see app/deploy.py. No parameters ever accepted. Originally gated only
    by the Settings panel's hidden long-press control with no real auth
    (an accepted Stage 6 risk, since it only ever ran this one fixed
    command) — now admin-only (frontend migration Part C3), on top of
    that same fixed-command bound."""
    try:
        result = run_git_pull()
    except DeployError as exc:
        store.record_auth_event("deploy_failed", ip_address=_client_ip(request), detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    store.record_auth_event(
        "deploy_triggered", ip_address=_client_ip(request), detail=f"now at {result.get('commit')}"
    )
    return result


# Wires the default-deny routers onto the app — see this module's own
# docstring for the small, explicit allowlist that stays directly on
# `app` instead (unauthenticated, or with its own bespoke gate).
app.include_router(router)
app.include_router(admin_router)
