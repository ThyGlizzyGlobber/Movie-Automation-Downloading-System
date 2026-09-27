"""What every route module shares: the app.state accessors and auth
dependencies routes declare with `Depends(...)`, the session/login cookie
helpers, the client-address and rate-limit plumbing, and the two
default-deny routers themselves (see this package's own docstring)."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.db import RequestStore, SessionRow
from app.plex import LoginSession, PlexLinker
from app.qbt import QBTClient
from app.tmdb import TMDBClient
from app.worker import Worker


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


def _set_session_cookie(response: Response, request: Request, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(request),
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        path="/",
    )


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


def resolve_region(region: str | None = None, store: RequestStore = Depends(get_store)) -> str:
    """Which country TMDB should answer for.

    Provider availability, digital release dates and what counts as
    Coming Soon are all region-scoped, and every one of those endpoints
    used to carry its own literal "US" default that no caller ever
    overrode. That quietly made the household's Region setting a
    ratings-only switch: an Australian household browsing Stan got an
    empty page, because TMDB lists 0 titles on provider 21 in the US
    against 613 in AU (checked 2026-09-26).

    An explicit ?region= still wins, so each endpoint stays addressable
    on its own terms and the existing tests that pin a region keep
    working. Absent one, the household's own setting answers, falling
    back to DEFAULT_CERTIFICATION_REGION when nothing has been chosen.
    """
    if region:
        return region.upper()
    return store.get_settings().get("certification_region") or DEFAULT_CERTIFICATION_REGION


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
    return require_admin(require_session(request, store))


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
# be the admin's. See app/api/__init__.py's docstring for the small, explicit
# allowlist that deliberately stays off both.
router = APIRouter(dependencies=[Depends(require_session)])
admin_router = APIRouter(dependencies=[Depends(require_admin)])
