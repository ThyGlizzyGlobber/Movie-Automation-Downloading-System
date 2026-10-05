"""End-user sign-in: the Plex PIN login, the current session, logout and
the tutorial flag."""

import secrets
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response

from app.db import RequestStore, SessionRow
from app.plex import PIN_TIMEOUT_SECONDS, LoginSession, PlexError
from app.api.deps import (
    DEFAULT_CERTIFICATION_REGION,
    LOGIN_ATTEMPT_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    STATUS_POLL_RATE_LIMIT,
    _client_ip,
    _cookie_secure,
    _new_session_expiry,
    _set_session_cookie,
    can_clear_requests,
    get_login_session,
    get_store,
    limiter,
    require_session,
    router,
)
from app.api.main import app


# ---------------------------------------------------------------------------
# End-user login (frontend migration Part C3) — Plex PIN sign-in, same
# mechanism as admin server-linking (app/api/plex.py), different purpose:
# authenticates *one person* against the already-linked server rather than
# linking the server itself. See app/plex.py's LoginSession.
# ---------------------------------------------------------------------------


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
        user = store.upsert_user(
            result["plex_user_id"],
            result["username"],
            result["is_admin"],
            result.get("thumb"),
            server_token=result.get("server_token"),
        )
        session_id = secrets.token_urlsafe(32)
        store.create_session(session_id, user.plex_user_id, user.username, user.is_admin, _new_session_expiry())
        _set_session_cookie(response, request, session_id)
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
        # Whether the Requests page shows "Clear finished" (Settings › History).
        "can_clear_requests": can_clear_requests(store, session),
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
