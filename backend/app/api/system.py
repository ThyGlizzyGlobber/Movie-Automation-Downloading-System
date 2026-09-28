"""Health, storage, about, and the admin's job/activity/audit views,
session revocation and self-update."""

import asyncio
import os
from pathlib import Path
import shutil
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app import config, version
from app.db import FAILURE_STATUSES, RequestStore
from app.deploy import DeployError, run_git_pull
from app.api.deps import _client_ip, admin_router, get_qbt, get_store, router
from app.api.schemas import RequestOut
from app.api.main import app


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
    of it each library holds, and request throughput counters. Stays
    async so the two library walks run side by side; everything else that
    blocks (disk stat, SQLite counts) goes to a thread with them."""
    disk, movie_bytes, tv_bytes, counters = await asyncio.gather(
        asyncio.to_thread(get_storage),
        asyncio.to_thread(_directory_bytes, config.MOVIE_LIBRARY_ROOT),
        asyncio.to_thread(_directory_bytes, config.TV_LIBRARY_ROOT),
        asyncio.to_thread(_request_counters, store),
    )
    return {
        **disk,
        "libraries": [
            {"key": "movies", "label": "Movies", "root": str(config.MOVIE_LIBRARY_ROOT), "bytes": movie_bytes},
            {"key": "tv", "label": "TV", "root": str(config.TV_LIBRARY_ROOT), "bytes": tv_bytes},
        ],
        **counters,
    }


def _request_counters(store: RequestStore) -> dict:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()
    return {
        "downloading": store.count_requests_with_status("downloading"),
        "queued": store.count_requests_with_status("queued") + store.count_requests_with_status("searching"),
        "completed_today": store.count_completed_since(day_start),
        "completed_week": store.count_completed_since(week_start),
    }


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
        **version.as_dict(),
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

@admin_router.get("/api/admin/jobs")
def admin_jobs(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[RequestOut]:
    statuses = [status] if status else sorted(FAILURE_STATUSES)
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
