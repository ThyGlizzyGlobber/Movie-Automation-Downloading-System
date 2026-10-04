"""Settings › Household: who can request, removing someone, and the
same-origin avatar proxy."""

import hashlib

import requests as _http_requests
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel

from app.cache import TTLCache
from app.db import RequestStore, SessionRow
from app.api.deps import _client_ip, admin_router, get_store, require_admin, require_session, router


# ---------------------------------------------------------------------------
# Household
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
    counts = {s["plex_user_id"]: s["total_requests"] for s in store.get_requester_stats()}
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
        _client_ip(request),
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
        _client_ip(request),
        f"{user.username or plex_user_id}: removed",
    )
    return {"removed": True}


_AVATAR_TTL = 60
_avatar_cache = TTLCache(_AVATAR_TTL, max_entries=64)


def _avatar_response(avatar_url: str | None, request: Request) -> RawResponse:
    """Proxies a plex.tv avatar through this origin (the CSP allows no
    other image host). plex.tv is asked at most once a minute, so a
    picture changed on Plex shows here within a minute.

    The browser revalidates every time against an ETag rather than the
    page asking for a new URL every minute, which is what it used to do:
    a Plex picture can be ~700KB, and every app open more than a minute
    after the last one downloaded all of it again, ahead of the posters.
    Unchanged, it is now a 304 with no body."""
    if not avatar_url or not avatar_url.startswith("https://plex.tv/"):
        raise HTTPException(status_code=404, detail="no avatar")
    cached, hit = _avatar_cache.get((avatar_url,))
    if not hit:
        try:
            upstream = _http_requests.get(avatar_url, timeout=10)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"avatar unavailable: {exc}") from exc
        if not upstream.ok:
            raise HTTPException(status_code=404, detail="no avatar")
        content = upstream.content
        cached = (content, upstream.headers.get("Content-Type", "image/png"), f'"{hashlib.sha256(content).hexdigest()[:20]}"')
        _avatar_cache.set((avatar_url,), cached)
    content, media_type, etag = cached
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return RawResponse(status_code=304, headers=headers)
    return RawResponse(content=content, media_type=media_type, headers=headers)


@router.get("/api/me/avatar")
def my_avatar(request: Request, session: SessionRow = Depends(require_session), store: RequestStore = Depends(get_store)) -> RawResponse:
    user = store.get_user(session.plex_user_id)
    return _avatar_response(user.avatar_url if user else None, request)


@admin_router.get("/api/admin/users/{plex_user_id}/avatar")
def household_avatar(plex_user_id: str, request: Request, store: RequestStore = Depends(get_store)) -> RawResponse:
    user = store.get_user(plex_user_id)
    return _avatar_response(user.avatar_url if user else None, request)
