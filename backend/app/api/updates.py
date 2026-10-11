"""Settings › Updates on a release install: what's running, what's newer on
this box's channel and what changed in it, and the button that installs it.
Installing is truenas/obsidian-updater.py's job, on the NAS itself; this side
copies the database and leaves the request (app/updates.py). On a git-pull
install there's no channel, and `POST /api/admin/deploy` (system.py) is the
update instead."""

import time

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app import updates, version
from app.db import RequestStore
from app.api.deps import _client_ip, admin_router, get_store

# How long a registry answer is reused before asking again. The button asks
# again regardless (`refresh`); this only spares the registry a lookup every
# time someone opens the page.
_CHECK_SECONDS = 600
_latest_cache: dict[str, tuple[float, str | None, str | None, list[dict]]] = {}


def _latest(channel: str, refresh: bool) -> tuple[str | None, str | None, list[dict]]:
    """(newest version on the channel, why that couldn't be found, its notes)."""
    cached = _latest_cache.get(channel)
    if cached and not refresh and time.monotonic() - cached[0] < _CHECK_SECONDS:
        return cached[1], cached[2], cached[3]
    try:
        latest, error = updates.published_version(channel), None
    except updates.RegistryError as exc:
        latest, error = None, str(exc)
    notes = updates.release_notes(latest) if latest and channel == "stable" else []
    _latest_cache[channel] = (time.monotonic(), latest, error, notes)
    return latest, error, notes


def _updater_status() -> dict:
    """The updater's state, and whether it's running on this box at all —
    it leaves a heartbeat every minute. `reachable` and `configured` say the
    same thing; both are kept for the page."""
    running = updates.updater_running()
    state = updates.updater_state()
    keys = ("state", "from_version", "to_version", "message", "started_at", "finished_at")
    return {"reachable": running, "configured": running, **{key: state.get(key) for key in keys}}


@admin_router.get("/api/admin/updates")
def get_updates(refresh: bool = False) -> dict:
    current = version.current()
    channel = updates.channel()
    if not channel:
        return {"method": "git", "current": current.version, "channel": None}
    latest, error, notes = _latest(channel, refresh)
    status = _updater_status()
    # A release that was just rolled back isn't offered again: installing it
    # would only fail the same way. The next one published is.
    skipped = status.get("to_version") if status.get("state") == "rolled_back" else None
    available = updates.is_newer(latest, current.version) and latest != skipped
    return {
        "method": "image",
        "current": current.version,
        "channel": channel,
        "latest": latest,
        "available": available,
        "skipped": skipped if latest == skipped else None,
        "notes": notes if available else [],
        "error": error,
        "updater": status,
    }


class InstallRequest(BaseModel):
    version: str


@admin_router.post("/api/admin/updates", status_code=202)
def install_update(request: Request, body: InstallRequest, store: RequestStore = Depends(get_store)) -> dict:
    current = version.current().version
    if not updates.channel():
        raise HTTPException(status_code=409, detail="This install updates with git pull")
    if not updates.is_newer(body.version, current):
        raise HTTPException(status_code=422, detail=f"{body.version} isn't newer than {current}")
    if not updates.updater_running():
        raise HTTPException(status_code=409, detail="Updates aren't set up on this box yet")
    if updates.updater_state().get("state") in updates.BUSY:
        raise HTTPException(status_code=409, detail="An update is already under way")
    try:
        published = updates.release_exists(body.version)
    except updates.RegistryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not published:
        raise HTTPException(status_code=404, detail=f"Version {body.version} isn't published")

    backup = updates.backup_database(store, current, body.version)
    state = updates.request_update(body.version, current, backup.name)
    store.record_auth_event(
        "update_started", ip_address=_client_ip(request), detail=f"{current} to {body.version}"
    )
    keys = ("state", "from_version", "to_version", "message", "started_at", "finished_at")
    return {"reachable": True, "configured": True, **{key: state.get(key) for key in keys}}
