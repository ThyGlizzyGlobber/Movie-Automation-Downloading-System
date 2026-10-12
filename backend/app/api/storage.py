"""Settings › Storage on a box: its drives, how full and how healthy each is,
and adding one. Adding erases the disk, so the request names it by name
and serial from the NAS updater's own latest report, and the updater
checks both again before touching it (truenas/obsidian-updater.py)."""

import os
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app import box_plex, drive_recovery, drives, updates
from app.api.deps import _client_ip, admin_router, get_store, get_tmdb, require_admin, require_session_server
from app.catalogue.tmdb import TMDBClient
from app.db import PlexServerRow, RequestStore, SessionRow

# The updater writes its report every minute; older than this, it's stale.
REPORT_FRESH_SECONDS = 180


def _report() -> dict:
    report = updates.read_json(updates.updater_dir() / "storage.json") or {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(report["at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = None
    report["fresh"] = age is not None and age < REPORT_FRESH_SECONDS
    return report


def _drive_rows(report: dict, store: RequestStore | None = None) -> list[dict]:
    """Every drive the box knows of: the ones mounted and marked here, and
    any the NAS still has a pool for that isn't (a drive that has failed
    or been pulled out)."""
    health = {d.get("id"): d for d in report.get("drives") or []}
    rows = []
    for drive in drives.all_drives():
        usage = drives.usage(drive)
        pool = health.pop(drive.id, None)
        problem = (pool is not None and not pool.get("healthy")) or not usage["readable"]
        rows.append({
            "id": drive.id, "label": drive.label, "size": drive.size_bytes or usage["total"],
            "used": usage["used"], "free": usage["free"],
            "state": "problem" if problem else "healthy",
            "status": pool.get("status") if pool else None,
        })
    for drive_id, pool in sorted(health.items(), key=lambda item: (len(str(item[0])), str(item[0]))):
        rows.append({
            "id": drive_id, "label": f"Drive {str(drive_id).removeprefix('drive')}", "size": pool.get("size"),
            "used": None, "free": None, "state": "missing", "status": pool.get("status"),
            # How many files this app filed on it, to download again if it
            # has failed.
            "lost_files": len(store.library_items_under(drive_recovery.drive_folder(drive_id)))
            if store is not None and drive_recovery.DRIVE_ID.match(str(drive_id)) else 0,
        })
    return rows


@admin_router.get("/api/admin/drives")
def get_drives(store: RequestStore = Depends(get_store)) -> dict:
    if not drives.managed():
        return {"managed": False}
    report = _report()
    pending = (updates.updater_dir() / "drive-request.json").exists()
    return {
        "managed": True,
        "drives": _drive_rows(report, store),
        # Only offered from a report fresh enough to trust.
        "unused": report.get("unused") or [] if report["fresh"] else [],
        "report_fresh": report["fresh"],
        "report_error": report.get("error"),
        "pending": pending,
        "last": updates.read_json(updates.updater_dir() / "drive-state.json"),
        "updater_running": updates.updater_running(),
    }


class AddDrive(BaseModel):
    disk: str
    serial: str
    # The person typed the confirmation: this erases the disk.
    confirm: bool = False
    # And, for a disk holding another system's data, agreed to erase that.
    erase_other_data: bool = False


@admin_router.post("/api/admin/drives", status_code=202)
def add_drive(request: Request, body: AddDrive, store: RequestStore = Depends(get_store)) -> dict:
    if not drives.managed():
        raise HTTPException(status_code=404, detail="This install's storage isn't managed from here")
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Confirm that everything on the drive will be erased")
    if not updates.updater_running():
        raise HTTPException(status_code=409, detail="The box's updater isn't running, so the drive can't be added now")
    report = _report()
    found = next((d for d in report.get("unused") or [] if d.get("name") == body.disk and d.get("serial") == body.serial), None)
    if not report["fresh"] or found is None:
        raise HTTPException(status_code=409, detail="That drive isn't free to add any more. Reload and try again.")
    if found.get("has_other_data") and not body.erase_other_data:
        raise HTTPException(status_code=422, detail="That drive holds data from another system. Confirm erasing it too.")
    path = updates.updater_dir() / "drive-request.json"
    if path.exists():
        raise HTTPException(status_code=409, detail="A drive is already being added")
    updates.write_json(path, {"disk": body.disk, "serial": body.serial, "erase_other_data": body.erase_other_data,
                              "requested_at": updates.now_iso()})
    os.chmod(path, 0o600)
    store.record_auth_event("drive_added", ip_address=_client_ip(request), detail=f"{body.disk} ({body.serial})")
    return {"pending": True}


def _missing_drive(drive_id: str) -> None:
    """Only a drive the box can't find any more: one that's there still
    has its files."""
    if not drives.managed():
        raise HTTPException(status_code=404, detail="This install's storage isn't managed from here")
    if not drive_recovery.DRIVE_ID.match(drive_id):
        raise HTTPException(status_code=404, detail="No such drive")
    row = next((d for d in _drive_rows(_report()) if d["id"] == drive_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="No such drive")
    if row["state"] != "missing":
        raise HTTPException(status_code=409, detail=f"{row['label']} is still there, with its files")


@admin_router.get("/api/admin/drives/{drive_id}/lost")
def get_lost(drive_id: str, store: RequestStore = Depends(get_store)) -> dict:
    """What was on a drive the box can't find: every title this app filed
    there."""
    _missing_drive(drive_id)
    found = drive_recovery.lost(store, drive_id)
    found.pop("_items")
    return found


@admin_router.post("/api/admin/drives/{drive_id}/lost")
def download_lost_again(
    request: Request,
    drive_id: str,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    session: SessionRow = Depends(require_admin),
    server: PlexServerRow = Depends(require_session_server),
) -> dict:
    """Asks for everything that was on a failed drive again, onto the
    drives that are left (app/drive_recovery.py). With none left, the
    requests wait until a drive is added, like any other; with downloads
    not running (qBittorrent unreachable for now), they wait queued and
    the worker picks them up when it starts."""
    _missing_drive(drive_id)
    if not store.library_items_under(drive_recovery.drive_folder(drive_id)):
        raise HTTPException(status_code=409, detail="Nothing on record was on that drive")
    result = drive_recovery.download_again(store, tmdb, drive_id, server.id, session.plex_user_id, session.username)
    # Plex stops listing the drive's folders now, before any of the new
    # requests asks it what it already has; the worker keeps trying if
    # Plex can't be reached (worker._watch_box_libraries).
    primary = store.primary_plex_server()
    if primary is not None:
        try:
            box_plex.sync_libraries(primary.url, primary.token, drop=box_plex.dropped_folders(store.get_settings()))
        except Exception:  # noqa: BLE001 — retried by the worker
            pass
    worker = request.app.state.worker
    for request_id in result["requested"] if worker is not None else ():
        worker.enqueue(request_id)
    store.record_auth_event(
        "drive_redownloaded", plex_user_id=session.plex_user_id, username=session.username,
        ip_address=_client_ip(request), detail=f"{drive_id}: {len(result['requested'])} request(s)",
    )
    return {"requested": len(result["requested"]), "joined": result["joined"], "failed": result["failed"]}
