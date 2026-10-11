"""Settings › Updates on a release install: what's running, what's newer on
this box's channel and what changed in it, and the button that installs it.
The installing itself is the updater sidecar's (app/updater.py); this side
copies the database first and hands over. On a git-pull install there's no
channel, and `POST /api/admin/deploy` (system.py) is the update instead."""

import time

import requests
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app import config, updates, version
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
    """The updater's own view when it answers. While it's being replaced
    mid-update it can't, so the state file it keeps on the shared volume
    stands in."""
    token = updates.shared_token()
    if token:
        try:
            response = requests.get(f"{config.UPDATER_URL}/status", headers={"X-Updater-Token": token}, timeout=5)
            if response.ok:
                return {"reachable": True, **response.json()}
        except (requests.RequestException, ValueError):
            pass
    state = updates.read_json(updates.state_path()) or {"state": "idle"}
    return {"reachable": False, "configured": None, **state}


def _public(status: dict) -> dict:
    keys = ("reachable", "configured", "state", "from_version", "to_version", "message", "started_at", "finished_at")
    return {key: status.get(key) for key in keys}


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
        "updater": _public(status),
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
    token = updates.shared_token()
    if not token:
        raise HTTPException(status_code=409, detail="The updater isn't running on this box")

    backup = updates.backup_database(store, current, body.version)
    try:
        response = requests.post(
            f"{config.UPDATER_URL}/update",
            json={"version": body.version, "from_version": current, "backup": backup.name},
            headers={"X-Updater-Token": token},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't reach the updater: {exc}") from exc
    if not response.ok:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        store.record_auth_event("deploy_failed", ip_address=_client_ip(request), detail=str(detail))
        raise HTTPException(status_code=response.status_code, detail=detail or "The updater refused")
    store.record_auth_event(
        "update_started", ip_address=_client_ip(request), detail=f"{current} to {body.version}"
    )
    return _public({"reachable": True, **response.json()})
