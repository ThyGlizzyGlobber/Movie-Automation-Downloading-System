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

import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path
import re
import secrets
import shutil

import requests as _http_requests
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, Response as RawResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import config, trailers
from app.cache import ttl_cache
from app.db import RequestRow, RequestStore, SessionRow, ShowRow
from app.deploy import DeployError, run_git_pull
from app.logging_config import configure_logging
from app.media_organizer import find_existing_episode_files
from app.pipeline_settings import (
    VALID_MIN_RESOLUTIONS,
    is_valid_min_resolution,
    resolve_pipeline_settings,
    settings_from_raw,
)
from app.plex import (
    PIN_TIMEOUT_SECONDS,
    LoginSession,
    PlexClient,
    PlexError,
    PlexLinker,
    local_file_for_title,
    locate_title,
    new_client_identifier,
    plex_library_lookup,
    plex_show_episodes,
)
from app.qbt import QBTClient
from app.resolve import resolve
from app.tmdb import BROWSE_SORTS, TMDBClient, TMDBError, best_logo_path, is_movie_coming_soon, is_tv_upcoming, trailer_candidates
from app.tv_resolve import episode_is_released, resolve_show
from app.tv_settings import resolve_tv_settings
from app.tvmaze import TVMazeClient, season_airstamps
from app.worker import Worker

# Cache filenames are always trailers.cached_trailer_path()'s own
# "{media_type}-{tmdb_id}-{key}.mp4" shape — validated before ever touching
# the filesystem so a crafted filename can't path-traverse out of
# TRAILER_CACHE_DIR.
_TRAILER_FILENAME_RE = re.compile(r"^[a-z]+-\d+-[\w-]+\.mp4$")

configure_logging()
logger = logging.getLogger("app.api")

# Which country's age ratings to show. TMDB carries certifications per
# region and they are not interchangeable — the same show is TV-MA in the
# US and MA15+ in Australia — so this decides which one the UI reads.
# Household-wide rather than per-person: it describes where this server
# is, not who is looking. "US" because TMDB's US data is the most
# complete, so it is the safest thing to fall back to.
DEFAULT_CERTIFICATION_REGION = "US"

SESSION_COOKIE_NAME = "session_id"
# Names the one browser allowed to claim a given in-flight Plex sign-in.
# Not a credential for anything else and never readable by JS: it only
# identifies *which* pending attempt the poller is asking after, and it
# is spent the moment that attempt is traded for a real session.
LOGIN_ATTEMPT_COOKIE_NAME = "login_attempt"
# Sliding expiry, not absolute — every successful `require_session` check
# could in principle refresh it, but isn't wired up (yet) to do so; a
# session simply needs re-establishing via login after this long regardless
# of activity. 14 days is a starting default, easy to change later.
SESSION_TTL_DAYS = 14


def _new_session_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()


def _cookie_secure(request: Request) -> bool:
    """Whether *this* request reached the browser over HTTPS.

    `Secure` is not a preference, it is a fact about one request, and
    getting it wrong in either direction costs a session: set it when the
    browser is on plain HTTP and the cookie is accepted and then never
    sent back, so signing in appears to work and the next call is a 401;
    omit it over HTTPS and the cookie is one downgrade away from leaking.

    It used to be read from the `remote_access_enabled` setting, which
    made it a single global answer to a per-request question. That was
    survivable while the app had one front door. It stopped being
    survivable the moment it had two: https://<domain> through the tunnel
    and http://<nas>:8095 on the LAN, both live, both legitimate. No value
    of that setting is right for both, and the one that was set turned
    every LAN sign-in into a silent 401.

    So ask the request. nginx sends X-Forwarded-Proto, deriving it from
    Cloudflare's edge for tunnel traffic and from its own listener
    otherwise — see the map at the top of frontend/nginx.conf for what
    that header is and is not worth trusting. The scheme is the fallback
    for anything with no proxy in front: the tests, and `uvicorn --reload`
    run directly in development."""
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        # A comma-separated chain is legal and only the first hop is the
        # browser's own; nothing here produces one today, but reading
        # past it would silently mean "whatever the last proxy felt like".
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _client_ip(request: Request) -> str | None:
    """The address of the browser that actually made this request.

    `request.client.host` is the peer that opened the TCP connection, and
    this app has exactly one of those: nginx. The backend publishes no
    port (docker-compose.yml's standing invariant that it never gains a
    `ports:` entry), so nothing else can reach it — which makes
    `request.client.host` the *same private address for every caller on
    earth*, and useless as an identity. Read live it silently answered
    two questions wrong: the audit log recorded a container address in
    place of whoever signed in, and the rate limiters below keyed every
    household member and every stranger into one shared bucket.

    nginx sends the real address in X-Real-IP. Trusting a header is only
    safe because of the invariant above plus `proxy_set_header`, which
    *replaces* any X-Real-IP a client tried to send rather than appending
    to it — so the value here cannot be chosen from outside. Publish the
    backend's port and both of those stop being true at once, and this
    becomes attacker-controlled input steering rate limits and audit
    records alike.

    Falls back to the peer when the header is absent, which is the shape
    of every test and of `uvicorn --reload` run directly in development:
    no proxy in front, so the peer genuinely is the client."""
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded.strip()
    return request.client.host if request.client else None


def _rate_limit_key(request: Request) -> str:
    """Per-client buckets, not one bucket for the whole deployment.

    slowapi's own `get_remote_address` is `request.client.host`, which is
    why this exists — see `_client_ip` above for why that is a constant
    here. With it, the limits below did the opposite of their job once
    remote access was on: two people signing in at once could exhaust a
    limit meant for one, and anyone on the internet could spend the
    household's entire login allowance without holding any credential.

    `get_remote_address` stays as the fallback so a request that somehow
    has neither header nor peer still lands on a key rather than raising
    inside the limiter."""
    return _client_ip(request) or get_remote_address(request)


def _return_url(request: Request) -> str | None:
    """Where plex.tv sends the browser back to once a sign-in is
    authorised — the equivalent of an OAuth redirect URI, and the whole
    reason the user no longer has to find their way back by hand.

    Taken from the browser's own `Origin` header rather than from
    anything the page put in the request: a page cannot forge the Origin
    the browser stamps on its own request, so there is no redirect
    parameter here for an attacker to aim somewhere else, and no
    allowlist to keep in sync with however this deployment is reached
    (LAN IP, hostname, tunnel domain — whichever the browser actually
    used is by definition the right one to come back to). Only the
    origin survives: any path, query or fragment is dropped rather than
    trusted, so the worst a bad Origin can do is send its own browser
    back to itself. `None` (no forwarding, plex.tv tells the user to
    return by hand) whenever that can't be established — every browser
    sends Origin on a POST, but a probe or a proxy that strips it
    shouldn't take sign-in down with it."""
    origin = request.headers.get("origin")
    if not origin:
        return None
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = RequestStore(config.DB_PATH)
    app.state.store = store
    app.state.started_at = datetime.now(timezone.utc)
    # Settings › Plex library folders saved from the UI (env still wins).
    config.apply_library_overrides(store)

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


app = FastAPI(title="Obsidian", lifespan=lifespan)

# Frontend migration Part G4 — rate limiting on the routes an internet
# attacker would actually script against once remote access is enabled:
# /api/auth/login/* (credential-guessing-shaped, even though "credential"
# here just means "does this Plex account have access") and /api/setup/*
# (the bootstrap race Part G1's LAN restriction already narrows, this is
# the second layer). In-memory, per-process — genuinely fine at household
# scale (one backend process, no multi-worker deployment), not meant to
# survive a restart. 20/minute is generous enough that no normal *user
# interaction* (including a slow multi-attempt login) ever brushes it,
# while still bounding a scripted attacker to a rate that isn't useful.
#
# The exception is /api/auth/login/status, which isn't a user
# interaction at all — it's the login page's own progress poll, one
# request every POLL_INTERVAL_MS for as long as the PIN is unresolved.
# At the original 20/minute it outran its own limit (24 polls/minute)
# and 429'd itself ~50s in, which read to the user as a login that
# silently hung. It gets STATUS_POLL_RATE_LIMIT instead: enough headroom
# for the poll, a couple of open tabs, and the retries that ride on top
# of it. Cheap to serve and nothing to guess at, so the looser bound
# costs us nothing an attacker can use.
limiter = Limiter(key_func=_rate_limit_key)
STATUS_POLL_RATE_LIMIT = "60/minute"
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


def require_can_request(
    session: SessionRow = Depends(require_session), store: RequestStore = Depends(get_store)
) -> SessionRow:
    """Settings › Household lets an admin turn requests off for someone;
    admins can always request."""
    user = store.get_user(session.plex_user_id)
    if user is not None and not user.is_admin and not user.can_request:
        raise HTTPException(status_code=403, detail="requests are turned off for this account")
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
    # frontend migration Part K1/K2: set only from the "Already on Plex"
    # confirmation modal. "overwrite" is rejected below (create_request)
    # unless this tmdb_id has a request this app itself organized on
    # record — never offered against a file only the fuzzy on_plex
    # title/year match found.
    redownload_mode: str | None = None

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
    # TMDB's status at the last follow check (see db.ShowRow.tmdb_status).
    tmdb_status: str | None = None
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


_tvmaze = TVMazeClient()


def _season_airstamps_for(tmdb_id: int, season_number: int, tmdb: TMDBClient) -> dict[int, datetime]:
    """`{episode_number: released_at_utc}` from TVmaze for one season, so
    the show page and the per-episode request route hold an episode for
    the same window the worker does — measured from its real release
    rather than midnight UTC on TMDB's date. `{}` on any failure, which
    falls back to the date rule (tv_resolve.episode_is_released)."""
    try:
        identity = resolve_show(tmdb_id, tmdb)
    except TMDBError:
        return {}
    return season_airstamps(_tvmaze.airstamps_for_show(identity.tvdb_id, identity.imdb_id), season_number)


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
        on_plex = bool(matcher(item.get(title_key) or "", year, item.get("id"))) if matcher else False
        annotated.append({**item, "on_plex": on_plex})
    return annotated


def _on_plex_for(title: str, year: int | None, media_type: str, store: RequestStore, tmdb_id: int | None = None) -> bool:
    matcher = plex_library_lookup(store, media_type)
    return bool(matcher(title, year, tmdb_id)) if matcher else False


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


@router.get("/api/discover")
def discover_browse(
    genre: int | None = None,
    provider: int | None = None,
    year: int | None = None,
    sort: str = "popular",
    region: str = "US",
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    """The browse page's one endpoint: every filter at once. Digital-
    availability filtered like the rows above."""
    if sort not in BROWSE_SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {', '.join(BROWSE_SORTS)}")
    try:
        data = tmdb.browse_movies(genre_id=genre, provider_id=provider, year=year, sort=sort, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


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


# ---------------------------------------------------------------------------
# Landing-page hero slides.
#
# The carousel used to build these itself: fetch trending, then fire a
# detail call *per slide* purely to learn the title logo, badge, cert,
# length and genres, because TMDB's list endpoints carry none of those.
# That put two round trips in front of the logo before its <img> could
# even be created — the trending list, then the detail call — so the
# artwork arrived about a second after everything around it, and the
# whole thing repeated for every person on every page load. Folding it
# into one cached call is what takes the logo off the critical path.
#
# Nothing here recomputes what the detail routes already work out: it
# calls them, in parallel, and keeps the handful of fields a hero slide
# actually shows. The badge/cert/length wording stays in the frontend's
# homeHero.ts, the one place it has ever lived.
# ---------------------------------------------------------------------------

HERO_SLIDE_COUNT = 5
# Well past the trending lists these are drawn from: what a hero costs is
# five detail calls, and what it shows changes far more slowly than five
# minutes. At 300s a household loading Home a few times an hour found it
# cold nearly every time and paid 600ms for the privilege — measured on
# the real server, where it was the slowest thing on the page by a factor
# of three, everything else being served from cache in single-digit ms.
#
# What that leaves stale is `on_plex`, and that one is noticed: it is
# what turns the hero's button from "Add to Plex" into "Watch now", so a
# title you just added would go on denying it for half an hour. It is
# refreshed per request instead — see _with_fresh_plex_state, which costs
# nothing the page wasn't paying anyway.
HERO_TTL_SECONDS = 1800

# Only what a slide renders. The detail payloads these come from carry
# credits, recommendations and watch providers too — a hero showing five
# of those would be megabytes for the sake of a logo and two lines.
_HERO_COMMON = ("id", "overview", "backdrop_path", "poster_path", "logo_path", "genres", "is_coming_soon", "on_plex")
_HERO_MOVIE_ONLY = ("title", "runtime", "release_date", "release_dates")
_HERO_TV_ONLY = ("name", "first_air_date", "content_ratings", "next_episode_to_air", "last_episode_to_air", "plex_complete")


def _hero_slide(detail: dict, media_type: str) -> dict:
    keys = _HERO_COMMON + (_HERO_MOVIE_ONLY if media_type == "movie" else _HERO_TV_ONLY)
    slide = {k: detail.get(k) for k in keys}
    slide["media_type"] = media_type
    if media_type == "tv":
        # Season numbers are all the "N seasons" line needs; the full
        # objects carry an overview and poster art apiece.
        slide["seasons"] = [{"season_number": se.get("season_number")} for se in detail.get("seasons") or []]
    return slide


def _hero_picks(kind: str, tmdb: TMDBClient) -> list[tuple[str, dict]]:
    """(media_type, list item) for the slides, most popular first. A title
    with no backdrop art can't be a hero, so that filter runs before the
    count is taken rather than after."""
    candidates: list[tuple[str, dict]] = []
    if kind in ("home", "movies"):
        candidates += [("movie", it) for it in tmdb.get_available_trending().get("results", [])]
    if kind in ("home", "tv"):
        candidates += [("tv", it) for it in tmdb.get_available_tv_trending().get("results", [])]
    usable = [c for c in candidates if c[1].get("backdrop_path")]
    usable.sort(key=lambda c: c[1].get("popularity") or 0, reverse=True)
    return usable[:HERO_SLIDE_COUNT]


@ttl_cache(HERO_TTL_SECONDS)
def _hero_slides_cached(kind: str, store: RequestStore, tmdb: TMDBClient) -> list[dict]:
    """Keyed on the kind plus the two singletons on app.state, so this
    holds three entries rather than one per title — which matters,
    because app.cache's TTLCache has no size bound and only drops an
    entry when it is next read. A per-title cache here would grow
    without end."""
    picks = _hero_picks(kind, tmdb)
    if not picks:
        return []

    def build(media_type: str, item: dict) -> dict | None:
        try:
            detail = (
                get_movie_detail(item["id"], store, tmdb)
                if media_type == "movie"
                else get_tv_detail(item["id"], store, tmdb)
            )
        except Exception:
            # One title TMDB won't answer for shouldn't cost the carousel
            # its other four slides.
            logger.warning("hero slide failed for %s %s", media_type, item.get("id"), exc_info=True)
            return None
        return _hero_slide(detail, media_type)

    # In parallel: five sequential TMDB round trips is the very latency
    # this endpoint exists to remove, and doing them one after another
    # server-side would simply move it rather than fix it.
    with concurrent.futures.ThreadPoolExecutor(max_workers=HERO_SLIDE_COUNT) as pool:
        built = list(pool.map(lambda p: build(*p), picks))
    return [s for s in built if s]


def _with_fresh_plex_state(slides: list[dict], store: RequestStore) -> list[dict]:
    """Re-answer `on_plex` for slides that came out of the cache.

    Everything else a slide carries — the logo, the genres, the rating —
    is as true half an hour later as it was when TMDB was asked. Whether
    the thing is on Plex is not: it is the difference between the hero
    offering "Watch now" and offering to add something you already have.

    Cheap enough to do on every request because it is the same cached
    whole-library snapshot the rest of the page annotates itself from
    (see plex.py's plex_library_lookup), taken once per media type here
    rather than once per slide. Copies rather than mutates: these dicts
    are the cache's own."""
    matchers: dict[str, object] = {}
    fresh = []
    for slide in slides:
        media_type = slide["media_type"]
        if media_type not in matchers:
            matchers[media_type] = plex_library_lookup(store, "movie" if media_type == "movie" else "show")
        matcher = matchers[media_type]
        date = slide.get("release_date") or slide.get("first_air_date") or ""
        year = int(date[:4]) if date[:4].isdigit() else None
        title = slide.get("title") or slide.get("name") or ""
        fresh.append({**slide, "on_plex": bool(matcher(title, year, slide["id"])) if matcher else False})
    return fresh


@router.get("/api/hero")
def hero_slides(kind: str = "home", store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)) -> list[dict]:
    """`kind` picks which landing page's carousel this is: mixed
    movies+TV for home, one or the other for the movies and TV pages."""
    if kind not in ("home", "movies", "tv"):
        raise HTTPException(status_code=422, detail="kind must be home, movies or tv")
    return _with_fresh_plex_state(_hero_slides_cached(kind, store, tmdb), store)


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
    on_plex = _on_plex_for(movie.get("title") or "", year, "movie", store, tmdb_id)
    tracked = bool(store.get_library_items(tmdb_id, "movie")) or store.get_latest_organized_request(tmdb_id, ("movie",)) is not None
    return {
        **movie,
        "on_plex": on_plex,
        # A file this app didn't add, but Plex can point at from here: enough
        # to offer "Replace it" and "This copy is broken" for it.
        "plex_file_available": bool(on_plex and not tracked and local_file_for_title(store, "movie", movie.get("title") or "", year, tmdb_id) is not None),
        "is_coming_soon": is_coming_soon,
        "logo_path": best_logo_path(movie.get("images")),
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
    path = trailers.resolve("movie", tmdb_id, trailer_candidates(videos))
    return {"url": f"/api/trailers/{path.name}" if path else None}


# -- Stage 14: TV browse surface — the show equivalent of the movie routes
#    above. Same thin-pass-through/on_plex-annotation/key-never-reaches-
#    the-browser rules. --


@router.get("/api/tv/discover")
def tv_discover_browse(
    genre: int | None = None,
    provider: int | None = None,
    year: int | None = None,
    sort: str = "popular",
    region: str = "US",
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    # Registered ahead of /api/tv/{tmdb_id} so the literal segment wins.
    if sort not in BROWSE_SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {', '.join(BROWSE_SORTS)}")
    try:
        data = tmdb.browse_tv(genre_id=genre, provider_id=provider, year=year, sort=sort, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


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
    on_plex = _on_plex_for(show.get("name") or "", year, "show", store, tmdb_id)
    # Every aired episode is already on Plex: the show page hides "Add
    # all to Plex" rather than offering a download that would add nothing.
    aired = _aired_episode_count(show)
    have = _plex_episode_count(store, show.get("name") or "", year, tmdb_id) if on_plex else None
    return {
        **show,
        "on_plex": on_plex,
        "plex_complete": bool(have is not None and have >= aired > 0),
        # How many episodes the household actually has, for the "On disk"
        # tiles. The ledger below counts files, which counts a
        # re-downloaded episode twice; this counts episodes.
        "plex_episode_count": have,
        "is_coming_soon": is_tv_upcoming(show),
        "logo_path": best_logo_path(show.get("images")),
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
    path = trailers.resolve("tv", tmdb_id, trailer_candidates(videos))
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
    session: SessionRow = Depends(require_can_request),
) -> RequestOut:
    try:
        identity = resolve(body.tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {body.tmdb_id} not found") from exc

    if (
        body.redownload_mode == "overwrite"
        and not store.get_library_items(body.tmdb_id, "movie")
        and store.get_latest_organized_request(body.tmdb_id, ("movie",)) is None
        and local_file_for_title(store, "movie", identity.title, identity.release_year, body.tmdb_id) is None
    ):
        # Defense in depth — never trust the frontend's button state
        # alone. "Overwrite" is only ever offered against a file this
        # app organized itself, or one Plex can point at from here —
        # never a guess from the fuzzy on_plex title/year match.
        raise HTTPException(
            status_code=400, detail="no file on record for this title, and Plex can't point at one from here"
        )

    row = store.create_request(
        tmdb_id=body.tmdb_id,
        title=identity.title,
        release_year=identity.release_year,
        query=body.query,
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


def _cancel_active_torrent(row: RequestRow, store: RequestStore, qbt: QBTClient) -> str | None:
    """Shared by `cancel` and `reject` for a "downloading"/"complete" row:
    validates status/torrent-on-record/still-in-qBittorrent, deletes the
    torrent and its files, marks the request "cancelled", and returns the
    torrent hash (the one extra thing `reject` needs on top of everything
    `cancel` already does)."""
    if row.status not in _TORRENT_CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"cannot cancel a request in status {row.status!r}")
    torrent_hash = (row.result or {}).get("torrent_hash")
    if not torrent_hash:
        if row.status != "downloading":
            raise HTTPException(status_code=409, detail="no torrent on record for this request")
        # The add never pinned down which torrent was ours, so there is
        # nothing to delete; just stop tracking it.
        store.update_status(row.id, "cancelled")
        return None
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


@router.post("/api/movies/{tmdb_id}/reject-current")
def reject_current_movie_copy(
    tmdb_id: int,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    session: SessionRow = Depends(require_can_request),
) -> dict:
    """"This copy is broken" for the copy of a movie on Plex. A copy this
    app filed is in the library ledger with its torrent hash and release
    name, however long ago and whether or not the request row still
    exists: the hash and name are blacklisted for the title and the file
    deleted. A copy this app never added is found through Plex instead
    and blacklisted by size and name, for what those are worth. Either
    way the fresh request that follows can't pick the same release."""
    try:
        identity = resolve(tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc

    removed: list[str] = []
    items = store.get_library_items(tmdb_id, "movie")
    if items:
        for item in items:
            if item.get("torrent_hash"):
                store.add_rejected_torrent(tmdb_id, item["torrent_hash"])
            if item.get("release_name"):
                store.add_rejected_release(tmdb_id, item["release_name"])
            elif item.get("size_bytes"):
                store.add_rejected_release(tmdb_id, Path(item["path"]).stem, item["size_bytes"])
            path = Path(item["path"])
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"couldn't delete {path.name}: {exc}") from exc
            store.remove_library_item(item["path"])
            removed.append(path.name)
    else:
        path = local_file_for_title(store, "movie", identity.title, identity.release_year, tmdb_id)
        if path is None:
            raise HTTPException(status_code=409, detail="Plex can't point at a file for this title from here")
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = None
        store.add_rejected_release(tmdb_id, path.stem, size_bytes)
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"couldn't delete {path.name}: {exc}") from exc
        removed.append(path.name)
    logger.info("movie %d (%s): current copy rejected by %s: %s", tmdb_id, identity.title, session.username, ", ".join(removed))
    return {"removed": removed}


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
    no torrent on record yet to reject. A completed download whose torrent
    qBittorrent has since removed (the usual case once seeding is done)
    is still rejected: the hash on record is blacklisted and the files
    this app filed for it are deleted, so the next search really does
    fetch a different copy."""
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    if row.status == "queued":
        raise HTTPException(status_code=409, detail="a queued request has no torrent yet to reject")
    if row.status not in _TORRENT_CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"cannot reject a request in status {row.status!r}")
    torrent_hash = (row.result or {}).get("torrent_hash")
    if not torrent_hash:
        raise HTTPException(status_code=409, detail="no torrent on record for this request")

    if qbt.torrent_info(torrent_hash) is not None:
        qbt.delete_torrent(torrent_hash, delete_files=True)
    else:
        for path in (row.result or {}).get("organized_paths") or []:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                logger.warning("reject: couldn't delete %s for request %d", path, request_id)
            store.remove_library_item(path)
    store.update_status(row.id, "cancelled")
    store.add_rejected_torrent(row.tmdb_id, torrent_hash)
    # The hash only rules out magnet listings; most winners are direct
    # .torrent links, which carry no hash to compare (Mutiny came straight
    # back, live 2026-09-17). The release's name and size rule it out
    # however it's listed.
    winner = (row.result or {}).get("winner") or {}
    if winner.get("fileName"):
        store.add_rejected_release(row.tmdb_id, winner["fileName"])
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
    existing = store.get_show_by_tmdb_id(body.tmdb_id)
    if existing is not None and existing.status == "watching":
        raise HTTPException(status_code=409, detail="already following this show")
    if existing is not None:
        # A paused row is the anchor a one-off season add left behind;
        # following the show now is just switching that row on.
        store.update_show_status(existing.id, "watching")
        row = store.get_show(existing.id)
    else:
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
    session: SessionRow = Depends(require_can_request),
) -> RequestOut:
    try:
        identity = resolve_show(tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
    show = store.get_show_by_tmdb_id(tmdb_id)
    if show is None:
        show = store.create_show(
            tmdb_id=tmdb_id, title=identity.title, status="paused", poster_path=identity.poster_path
        )
    # "Add all to Plex" on a show that is still going also follows it, so
    # the episodes that haven't aired yet keep arriving; a one-off season
    # add, or a show that has ended, leaves following alone.
    if body.scope == "series" and not identity.ended and show.status != "watching":
        store.update_show_status(show.id, "watching")
        show = store.get_show(show.id)
        logger.info("show %d (%s): whole-series add on a returning show — now following", show.id, show.title)

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


def _pipeline_settings_out(store: RequestStore) -> dict:
    # The free-space floor is edited on the Storage card (library
    # settings), so it stays out of this panel's own round trip.
    out = asdict(resolve_pipeline_settings(store))
    out.pop("free_space_floor_gb", None)
    return out


@admin_router.get("/api/settings/pipeline")
def get_pipeline_settings(store: RequestStore = Depends(get_store)) -> dict:
    return _pipeline_settings_out(store)


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
    return _pipeline_settings_out(store)


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
            user = store.upsert_user(plex_user_id, identity.get("username"), True, identity.get("thumb"))
            session_id = secrets.token_urlsafe(32)
            store.create_session(session_id, user.plex_user_id, user.username, True, _new_session_expiry())
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=session_id,
                httponly=True,
                samesite="strict",
                secure=_cookie_secure(request),
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
async def start_login(
    request: Request,
    response: Response,
    login: LoginSession = Depends(get_login_session),
    store: RequestStore = Depends(get_store),
) -> dict:
    try:
        attempt_id, auth_url = await login.start(_return_url(request))
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # The attempt id goes back to this browser and nowhere else — it is
    # what /status checks before trading a resolved PIN for a session,
    # so it travels as an HttpOnly cookie rather than in the body where
    # page scripts (or anything that can read them) could pick it up.
    response.set_cookie(
        key=LOGIN_ATTEMPT_COOKIE_NAME,
        value=attempt_id,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(request),
        max_age=PIN_TIMEOUT_SECONDS,
        path="/",
    )
    return {"auth_url": auth_url}


@app.get("/api/auth/login/status")
@limiter.limit(STATUS_POLL_RATE_LIMIT)
def login_status(
    request: Request,
    response: Response,
    login: LoginSession = Depends(get_login_session),
    store: RequestStore = Depends(get_store),
) -> dict:
    # Only the browser holding this attempt's id gets an answer about
    # it. Everyone else — including an attacker polling this deliberately
    # unauthenticated route in the hope of catching someone else's
    # sign-in as it lands — is told there is simply nothing pending.
    attempt_id = request.cookies.get(LOGIN_ATTEMPT_COOKIE_NAME)
    status = login.status(attempt_id)
    result = status["result"]
    user = None
    if result:
        # Spend the attempt before minting anything: one resolved PIN is
        # one session, and a replayed poll finds nothing left to claim.
        login.finish(attempt_id)
        user = store.upsert_user(result["plex_user_id"], result["username"], result["is_admin"], result.get("thumb"))
        session_id = secrets.token_urlsafe(32)
        store.create_session(session_id, user.plex_user_id, user.username, user.is_admin, _new_session_expiry())
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            httponly=True,
            samesite="strict",
            secure=_cookie_secure(request),
            max_age=SESSION_TTL_DAYS * 24 * 3600,
            path="/",
        )
        # Spent server-side above; clear the browser's copy too rather
        # than leave a dead id sitting in the jar for its full 15 minutes.
        response.delete_cookie(LOGIN_ATTEMPT_COOKIE_NAME, path="/")
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
        # The avatar itself is served by /api/me/avatar (same origin).
        "avatar": bool(user and user.avatar_url),
        # Not really session state, but every page needs it before it can
        # render an age rating and this is already the first call each
        # client makes — a second boot request for one string would be a
        # round trip in front of the first thing anyone sees.
        "certification_region": store.get_settings().get("certification_region") or DEFAULT_CERTIFICATION_REGION,
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
#    displays the configured public URL back to the admin, and records the
#    change in the audit log. Defaults to False/None, same "opt-in,
#    admin-only, after the fact" principle Part G1's LAN-only setup
#    restriction exists to protect in the first place — an admin can only
#    ever reach this toggle once they're already authenticated, which
#    itself required completing setup from the LAN.
#
#    It no longer drives _cookie_secure(). It used to, and that was the
#    bug: a global setting answering a question that is per-request once
#    the app is reachable both over the tunnel and over plain HTTP on the
#    LAN. Enabling it made every LAN sign-in mint a cookie the browser
#    threw away. The cookie flag now comes from the request's own scheme,
#    so this setting is a note to the admin about where they published it
#    and an audited record that they did — nothing about a session
#    depends on it. --


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



NON_TERMINAL_STATUSES = {"queued", "searching", "downloading"}
_EPISODE_TERMINAL_FAILURES = {"failed", "no qualifying results", "insufficient free space", "downloaded, not filed", "cancelled"}


@router.get("/api/tv/{tmdb_id}/season/{season_number}/episodes")
def get_season_episodes(
    tmdb_id: int,
    season_number: int,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    """One season's episodes with what this household has of each —
    `in_plex` (a request this app filed, or a matching file already under
    the TV library), `requested` (a live per-episode or covering pack
    request, with its status/progress), `failed` (the last attempt ended
    badly), `unaired`, or `missing`. Backs the show page's episode list."""
    try:
        episodes = tmdb.get_tv_season(tmdb_id, season_number)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"season {season_number} of tmdb_id {tmdb_id} not found") from exc
    show = store.get_show_by_tmdb_id(tmdb_id)
    per_episode: dict[int, RequestRow] = {}
    covering_packs: list[RequestRow] = []
    if show is not None:
        for ledger in store.list_show_episodes(show.id):
            if ledger.season_number == season_number:
                row = store.get_request(ledger.request_id)
                if row is not None:
                    per_episode[ledger.episode_number] = row
        for row in store.list_requests():
            if row.media_type != "pack" or row.show_id != show.id or row.status not in NON_TERMINAL_STATUSES:
                continue
            start = row.season_number
            end = row.season_range_end if row.season_range_end is not None else start
            if start is None or (start <= season_number <= end):
                covering_packs.append(row)
    numbers = [e.get("episode_number") for e in episodes if e.get("episode_number") is not None]
    on_disk: dict[int, object] = {}
    try:
        identity = resolve_show(tmdb_id, tmdb)
        on_disk = find_existing_episode_files(identity, season_number, numbers)
    except (TMDBError, OSError):
        on_disk = {}
    # The air buffer the subscription scheduler honours, applied here too.
    # Without it this list called an episode requestable from UTC midnight
    # on its air_date while the scheduler wouldn't touch it for hours yet,
    # so the show page offered an "Add to Plex" button straight into the
    # exact window the setting exists to sit out.
    today = datetime.now(timezone.utc).date().isoformat()
    buffer_hours = resolve_tv_settings(store).episode_air_buffer_hours
    stamps = _season_airstamps_for(tmdb_id, season_number, tmdb) if buffer_hours else {}
    out = []
    for ep in episodes:
        number = ep.get("episode_number")
        row = per_episode.get(number)
        air_date = ep.get("air_date")
        state, status, progress, request_id = "missing", None, None, None
        if row is not None and row.status == "complete":
            state, status, request_id = "in_plex", row.status, row.id
        elif number in on_disk:
            state = "in_plex"
        elif row is not None and row.status in NON_TERMINAL_STATUSES:
            state, status, progress, request_id = "requested", row.status, row.download_progress, row.id
        elif covering_packs:
            pack = covering_packs[0]
            state, status, progress, request_id = "requested", pack.status, pack.download_progress, pack.id
        elif row is not None and row.status in _EPISODE_TERMINAL_FAILURES:
            state, status, request_id = "failed", row.status, row.id
        elif not air_date or air_date > today:
            state = "unaired"
        elif not episode_is_released(air_date, stamps.get(number), buffer_hours=buffer_hours):
            # Out, but inside the air buffer — the scheduler is
            # deliberately waiting, so the page says so rather than
            # offering a button that would jump the queue.
            state = "holding"
        out.append(
            {
                "episode_number": number,
                "name": ep.get("name"),
                "overview": ep.get("overview"),
                "air_date": air_date,
                "runtime": ep.get("runtime"),
                "still_path": ep.get("still_path"),
                "state": state,
                "status": status,
                "download_progress": progress,
                "request_id": request_id,
            }
        )
    in_plex = sum(1 for e in out if e["state"] == "in_plex")
    aired = sum(1 for e in out if e["state"] != "unaired")
    return {"season_number": season_number, "episodes": out, "in_plex": in_plex, "aired": aired}



@router.post("/api/tv/{tmdb_id}/episodes/{season_number}/{episode_number}", status_code=201)
def request_episode(
    tmdb_id: int,
    season_number: int,
    episode_number: int,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
    session: SessionRow = Depends(require_can_request),
) -> RequestOut:
    """Ask for one specific episode (the show page's per-row Request
    button). Same ledger the subscription scheduler uses, so the two never
    double-request the same episode; 409 if it is already tracked."""
    show = store.get_show_by_tmdb_id(tmdb_id)
    if show is None:
        try:
            identity = resolve_show(tmdb_id, tmdb)
        except TMDBError as exc:
            raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
        show = store.create_show(tmdb_id=tmdb_id, title=identity.title, status="paused", poster_path=identity.poster_path)
    if store.has_show_episode(show.id, season_number, episode_number):
        raise HTTPException(status_code=409, detail="this episode is already tracked")

    # The air buffer is a safety setting, not a scheduling preference: it
    # exists because the hours right after an air_date rolls over are when
    # fake and empty releases get uploaded to catch automated tools
    # searching too early. Enforcing it only in the worker left this route
    # as a way around it — and the show page was offering the button.
    buffer_hours = resolve_tv_settings(store).episode_air_buffer_hours
    if buffer_hours:
        try:
            episodes = tmdb.get_tv_season(tmdb_id, season_number)
        except TMDBError:
            episodes = []
        air_date = next(
            (e.get("air_date") for e in episodes if e.get("episode_number") == episode_number), None
        )
        stamps = _season_airstamps_for(tmdb_id, season_number, tmdb)
        if air_date and not episode_is_released(air_date, stamps.get(episode_number), buffer_hours=buffer_hours):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"this episode aired too recently — it's held for {buffer_hours:g}h after its air date "
                    "so a real release has time to appear (Settings › TV scheduling)"
                ),
            )

    row = store.create_episode_request(
        tmdb_id=show.tmdb_id,
        show_id=show.id,
        title=show.title,
        season_number=season_number,
        episode_number=episode_number,
        poster_path=show.poster_path,
    )
    store.add_show_episode(show.id, season_number, episode_number, row.id)
    logger.info(
        "episode request %d: %s S%02dE%02d by %s", row.id, show.title, season_number, episode_number, session.username
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


_LIBRARY_SIZE_TTL_SECONDS = 600
_library_size_cache: dict[str, tuple[float, int | None]] = {}


def _directory_bytes(root) -> int | None:
    """Total file bytes under `root`, or None when it isn't mounted.
    Walks with os.scandir (no stat of directories beyond entries), cached
    for ten minutes per root — a NAS library can be tens of thousands of
    files, which is fine every ten minutes but not per page load."""
    key = str(root)
    now = time.monotonic()
    cached = _library_size_cache.get(key)
    if cached and now - cached[0] < _LIBRARY_SIZE_TTL_SECONDS:
        return cached[1]
    if not os.path.isdir(root):
        _library_size_cache[key] = (now, None)
        return None
    total = 0
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    _library_size_cache[key] = (now, total)
    return total


@router.get("/api/storage/details")
async def get_storage_details(store: RequestStore = Depends(get_store)) -> dict:
    """The Settings storage panel: the disk from /api/storage plus how much
    of it each library holds, and request throughput counters."""
    disk = get_storage()
    movie_bytes, tv_bytes = await asyncio.gather(
        asyncio.to_thread(_directory_bytes, config.MOVIE_LIBRARY_ROOT),
        asyncio.to_thread(_directory_bytes, config.TV_LIBRARY_ROOT),
    )
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()
    return {
        **disk,
        "libraries": [
            {"key": "movies", "label": "Movies", "root": str(config.MOVIE_LIBRARY_ROOT), "bytes": movie_bytes},
            {"key": "tv", "label": "TV", "root": str(config.TV_LIBRARY_ROOT), "bytes": tv_bytes},
        ],
        "downloading": store.count_requests_with_status("downloading"),
        "queued": store.count_requests_with_status("queued") + store.count_requests_with_status("searching"),
        "completed_today": store.count_completed_since(day_start),
        "completed_week": store.count_completed_since(week_start),
    }


_ON_DECK_MAX = 12
_show_tmdb_cache: dict[str, tuple[float, int | None]] = {}
_SHOW_TMDB_TTL_SECONDS = 3600


def _tmdb_id_from_guids(item: dict | None) -> int | None:
    for guid in (item or {}).get("Guid", []) or []:
        value = guid.get("id", "")
        if value.startswith("tmdb://"):
            try:
                return int(value[len("tmdb://") :])
            except ValueError:
                return None
    return None


def _show_tmdb_id(client: PlexClient, url: str, token: str, rating_key: str | None) -> int | None:
    """TMDB id for one library item via its own metadata (cached an hour
    on success; a miss is retried next time, since Plex may still be
    matching a fresh item)."""
    if not rating_key:
        return None
    now = time.monotonic()
    cached = _show_tmdb_cache.get(rating_key)
    if cached and now - cached[0] < _SHOW_TMDB_TTL_SECONDS:
        return cached[1]
    try:
        tmdb_id = _tmdb_id_from_guids(client.metadata(url, token, rating_key))
    except Exception:  # noqa: BLE001 — transport errors just mean "unknown for now"
        return None
    if tmdb_id is not None:
        _show_tmdb_cache[rating_key] = (now, tmdb_id)
    return tmdb_id


_ON_DECK_LOOKUP_BUDGET_SECONDS = 6.0


def _resolve_tmdb_ids(client: PlexClient, url: str, token: str, rating_keys: set[str]) -> dict[str, int | None]:
    """Metadata lookups for several items at once, under one time budget
    — a slow Plex server degrades to "no link" for the stragglers rather
    than stalling the Home page."""
    results: dict[str, int | None] = {}
    if not rating_keys:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_show_tmdb_id, client, url, token, key): key for key in rating_keys}
        done, _ = concurrent.futures.wait(futures, timeout=_ON_DECK_LOOKUP_BUDGET_SECONDS)
        for future in done:
            try:
                results[futures[future]] = future.result()
            except Exception:  # noqa: BLE001
                results[futures[future]] = None
        pool.shutdown(wait=False, cancel_futures=True)
    return results


@router.get("/api/plex/on-deck")
def get_plex_on_deck(store: RequestStore = Depends(get_store)) -> dict:
    """Plex's Continue Watching for the linked server, shaped for the Home
    row: progress fraction, minutes left, the TMDB id (so a card can open
    this app's own detail page) and a same-origin artwork URL. Degrades
    to `available: false` rather than erroring — it backs a Home row, not
    a page anyone navigated to on purpose."""
    settings = store.get_settings()
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        return {"available": False, "items": []}
    client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
    try:
        raw = client.on_deck(url, token)
    except Exception as exc:  # noqa: BLE001 — any transport failure degrades the row, never the page
        logger.info("plex on-deck unavailable: %s", exc)
        return {"available": False, "items": []}
    entries = [e for e in raw[: _ON_DECK_MAX] if e.get("type") in ("movie", "episode")]
    # Ids the listing didn't carry: a show's (for an episode) or a movie's
    # own when includeGuids gave nothing — fetched together, bounded.
    lookup_keys = {
        str(e.get("grandparentRatingKey") if e.get("type") == "episode" else e.get("ratingKey"))
        for e in entries
        if e.get("type") == "episode" or _tmdb_id_from_guids(e) is None
    }
    resolved = _resolve_tmdb_ids(client, url, token, lookup_keys)
    items = []
    for entry in entries:
        kind = entry.get("type")
        duration = entry.get("duration") or 0
        offset = entry.get("viewOffset") or 0
        progress = round(min(max(offset / duration, 0.0), 1.0), 3) if duration else 0.0
        if kind == "episode":
            tmdb_id = resolved.get(str(entry.get("grandparentRatingKey")))
            art = entry.get("thumb") or entry.get("art") or entry.get("grandparentArt")
        else:
            tmdb_id = _tmdb_id_from_guids(entry) or resolved.get(str(entry.get("ratingKey")))
            art = entry.get("art") or entry.get("thumb")
        items.append(
            {
                "rating_key": str(entry.get("ratingKey")),
                "type": kind,
                "media_type": "tv" if kind == "episode" else "movie",
                "title": entry.get("title"),
                "show_title": entry.get("grandparentTitle"),
                "season_number": entry.get("parentIndex"),
                "episode_number": entry.get("index"),
                "year": entry.get("year"),
                "progress": progress,
                "remaining_minutes": max(0, round((duration - offset) / 60000)) if duration else None,
                "tmdb_id": tmdb_id,
                "art_url": f"/api/plex/image?path={art}" if art else None,
            }
        )
    return {"available": True, "machine_id": settings.get("plex_server_machine_id"), "items": items}


@router.get("/api/plex/locate")
def plex_locate(
    type: str, title: str, year: int | None = None, tmdb_id: int | None = None, store: RequestStore = Depends(get_store)
) -> dict:
    """Where a title lives on the linked server, for the detail page's
    Play button: {available, rating_key, machine_id}. Degrades to
    available: false rather than erroring."""
    if type not in ("movie", "show"):
        raise HTTPException(status_code=400, detail="type must be movie or show")
    settings = store.get_settings()
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        return {"available": False}
    client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
    try:
        found = locate_title(store, client, type, title, year, tmdb_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("plex locate unavailable: %s", exc)
        return {"available": False}
    if not found:
        return {"available": False}
    return {"available": True, "machine_id": settings.get("plex_server_machine_id"), **found}


@router.get("/api/plex/recently-added")
def get_plex_recently_added(store: RequestStore = Depends(get_store)) -> dict:
    """Plex's Recently Added, shaped for the Home row: one card per movie
    or show (a season or episode collapses onto its show), the TMDB id
    when it can be resolved, and a same-origin poster URL. Degrades to
    `available: false` like the on-deck route."""
    settings = store.get_settings()
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        return {"available": False, "items": []}
    client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
    try:
        raw = client.recently_added(url, token)
    except Exception as exc:  # noqa: BLE001
        logger.info("plex recently-added unavailable: %s", exc)
        return {"available": False, "items": []}
    entries = [e for e in raw if e.get("type") in ("movie", "show", "season", "episode")]

    def show_key(e: dict) -> str | None:
        if e.get("type") == "season":
            return str(e.get("parentRatingKey"))
        if e.get("type") == "episode":
            return str(e.get("grandparentRatingKey"))
        return None

    lookup_keys = {show_key(e) or str(e.get("ratingKey")) for e in entries if show_key(e) or _tmdb_id_from_guids(e) is None}
    resolved = _resolve_tmdb_ids(client, url, token, {k for k in lookup_keys if k and k != "None"})
    items: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        kind = entry.get("type")
        key = show_key(entry) or str(entry.get("ratingKey"))
        if key in seen:
            continue
        seen.add(key)
        if kind == "season":
            title, year, poster = entry.get("parentTitle"), entry.get("parentYear"), entry.get("parentThumb")
        elif kind == "episode":
            title, year, poster = entry.get("grandparentTitle"), None, entry.get("grandparentThumb")
        else:
            title, year, poster = entry.get("title"), entry.get("year"), entry.get("thumb")
        tmdb_id = resolved.get(key) if show_key(entry) else (_tmdb_id_from_guids(entry) or resolved.get(key))
        items.append(
            {
                "rating_key": key,
                "media_type": "movie" if kind == "movie" else "tv",
                "title": title,
                "year": year,
                "added_at": entry.get("addedAt"),
                "tmdb_id": tmdb_id,
                "poster_url": f"/api/plex/image?path={poster}&width=300&height=450" if poster else None,
            }
        )
    return {"available": True, "items": items}


@router.get("/api/plex/image")
def get_plex_image(path: str, width: int = 640, height: int = 360, store: RequestStore = Depends(get_store)) -> RawResponse:
    """Same-origin proxy for Plex library artwork — see PlexClient.fetch_image."""
    if not path.startswith("/library/") or ".." in path:
        raise HTTPException(status_code=400, detail="not a library image path")
    settings = store.get_settings()
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        raise HTTPException(status_code=404, detail="Plex is not linked")
    client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
    try:
        content, content_type = client.fetch_image(url, token, path, min(width, 1280), min(height, 720))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Plex image unavailable: {exc}") from exc
    return RawResponse(content=content, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------------------
# Household, library settings, about
# ---------------------------------------------------------------------------


class HouseholdUserOut(BaseModel):
    plex_user_id: str
    username: str | None
    is_admin: bool
    avatar: bool = False
    can_request: bool
    first_seen_at: str
    last_login_at: str
    requests: int


class HouseholdUserIn(BaseModel):
    can_request: bool


@admin_router.get("/api/admin/users")
def list_household(store: RequestStore = Depends(get_store)) -> list[HouseholdUserOut]:
    counts: dict[str, int] = {}
    for r in store.list_requests():
        if r.requested_by_plex_id:
            counts[r.requested_by_plex_id] = counts.get(r.requested_by_plex_id, 0) + 1
    return [
        HouseholdUserOut(
            plex_user_id=u.plex_user_id,
            username=u.username,
            is_admin=u.is_admin,
            avatar=bool(u.avatar_url),
            can_request=u.can_request,
            first_seen_at=u.first_seen_at,
            last_login_at=u.last_login_at,
            requests=counts.get(u.plex_user_id, 0),
        )
        for u in store.list_users()
    ]


@admin_router.put("/api/admin/users/{plex_user_id}")
def update_household_user(
    plex_user_id: str,
    body: HouseholdUserIn,
    request: Request,
    session: SessionRow = Depends(require_admin),
    store: RequestStore = Depends(get_store),
) -> dict:
    user = store.get_user(plex_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="the admin can always request")
    store.set_user_flags(plex_user_id, can_request=body.can_request)
    store.record_auth_event(
        "household_changed",
        session.plex_user_id,
        session.username,
        request.client.host if request.client else None,
        f"{user.username or plex_user_id}: requests {'on' if body.can_request else 'off'}",
    )
    return {"plex_user_id": plex_user_id, "can_request": body.can_request}


@admin_router.delete("/api/admin/users/{plex_user_id}")
def remove_household_user(
    plex_user_id: str,
    request: Request,
    session: SessionRow = Depends(require_admin),
    store: RequestStore = Depends(get_store),
) -> dict:
    user = store.get_user(plex_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="the admin cannot be removed")
    store.delete_user(plex_user_id)
    store.record_auth_event(
        "household_changed",
        session.plex_user_id,
        session.username,
        request.client.host if request.client else None,
        f"{user.username or plex_user_id}: removed",
    )
    return {"removed": True}


_AVATAR_TTL = 60


def _avatar_response(avatar_url: str | None) -> RawResponse:
    """Proxies a plex.tv avatar through this origin (the CSP allows no
    other image host) with a one-minute cache, so a picture changed on
    Plex shows here within a minute."""
    if not avatar_url or not avatar_url.startswith("https://plex.tv/"):
        raise HTTPException(status_code=404, detail="no avatar")
    try:
        upstream = _http_requests.get(avatar_url, timeout=10)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"avatar unavailable: {exc}") from exc
    if not upstream.ok:
        raise HTTPException(status_code=404, detail="no avatar")
    return RawResponse(
        content=upstream.content,
        media_type=upstream.headers.get("Content-Type", "image/png"),
        headers={"Cache-Control": f"private, max-age={_AVATAR_TTL}"},
    )


@router.get("/api/me/avatar")
def my_avatar(session: SessionRow = Depends(require_session), store: RequestStore = Depends(get_store)) -> RawResponse:
    user = store.get_user(session.plex_user_id)
    return _avatar_response(user.avatar_url if user else None)


@admin_router.get("/api/admin/users/{plex_user_id}/avatar")
def household_avatar(plex_user_id: str, store: RequestStore = Depends(get_store)) -> RawResponse:
    user = store.get_user(plex_user_id)
    return _avatar_response(user.avatar_url if user else None)


class LibrarySettingsIn(BaseModel):
    movie_library_root: str | None = None
    tv_library_root: str | None = None
    plex_refresh_after_import: bool = True
    free_space_floor_gb: float = Field(default=0, ge=0)


def _library_settings_out(store: RequestStore) -> dict:
    settings = store.get_settings()
    return {
        "movie_library_root": str(config.MOVIE_LIBRARY_ROOT),
        "tv_library_root": str(config.TV_LIBRARY_ROOT),
        "source": config.library_root_source(),
        "plex_refresh_after_import": settings.get("plex_refresh_after_import", True) is not False,
        "free_space_floor_gb": float(settings.get("free_space_floor_gb") or 0),
    }


class RegionSettingsIn(BaseModel):
    # Shape only, not an allowlist: which regions are worth offering is a
    # UI question, and TMDB adds certification bodies without asking. A
    # region it has nothing for simply falls back — see the frontend's
    # certificationOf.
    certification_region: str = Field(pattern=r"^[A-Z]{2}$")


@admin_router.get("/api/settings/region")
def get_region_settings(store: RequestStore = Depends(get_store)) -> dict:
    return {"certification_region": store.get_settings().get("certification_region") or DEFAULT_CERTIFICATION_REGION}


@admin_router.put("/api/settings/region")
def set_region_settings(body: RegionSettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    store.update_settings({"certification_region": body.certification_region})
    return {"certification_region": body.certification_region}


@admin_router.get("/api/settings/library")
def get_library_settings(store: RequestStore = Depends(get_store)) -> dict:
    return _library_settings_out(store)


@admin_router.put("/api/settings/library")
def set_library_settings(body: LibrarySettingsIn, store: RequestStore = Depends(get_store)) -> dict:
    patch: dict = {
        "plex_refresh_after_import": body.plex_refresh_after_import,
        "free_space_floor_gb": body.free_space_floor_gb,
    }
    if config.library_root_source() == "db":
        for key, value in (("movie_library_root", body.movie_library_root), ("tv_library_root", body.tv_library_root)):
            if value is not None:
                cleaned = value.strip()
                if not cleaned.startswith("/"):
                    raise HTTPException(status_code=422, detail=f"{key} must be an absolute path")
                patch[key] = cleaned
    store.update_settings(patch)
    config.apply_library_overrides(store)
    return _library_settings_out(store)


def _app_version() -> str | None:
    import subprocess

    env = os.environ.get("APP_VERSION")
    if env:
        return env
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/about")
def about(request: Request, store: RequestStore = Depends(get_store)) -> dict:
    import platform

    settings = store.get_settings()
    started = getattr(request.app.state, "started_at", None)
    try:
        db_bytes = os.path.getsize(config.DB_PATH)
    except OSError:
        db_bytes = None
    return {
        "name": "Obsidian",
        "version": _app_version(),
        "python": platform.python_version(),
        "started_at": started.isoformat() if started else None,
        "uptime_seconds": int((datetime.now(timezone.utc) - started).total_seconds()) if started else None,
        "plex_server_name": settings.get("plex_server_name"),
        "requests": store.count_requests(),
        "users": len(store.list_users()),
        "db_bytes": db_bytes,
        "movie_library_root": str(config.MOVIE_LIBRARY_ROOT),
        "tv_library_root": str(config.TV_LIBRARY_ROOT),
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
