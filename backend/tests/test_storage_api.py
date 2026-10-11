"""Settings › Storage on a box (app/api/storage.py), and the storage figures
the rest of the app shows, added up across a box's drives."""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import api, config, drives, updates
from app.api import deps
from app.db import RequestStore

TB = 1000**4


def add_drive(media, name, size=8 * TB):
    folder = media / name
    for sub in ("downloads/movies", "downloads/tv", "Movies", "TV Shows"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    (folder / drives.MARKER).write_text(json.dumps({"label": name.replace("drive", "Drive "), "size_bytes": size}))


def report(unused=(), pools=(), age=0, **extra):
    at = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(timespec="seconds")
    updates.write_json(updates.updater_dir() / "storage.json", {"at": at, "unused": list(unused), "drives": list(pools), **extra})


def disk(name="sdb", serial="WD-AAA", other=False):
    return {"name": name, "serial": serial, "size": 8 * TB, "type": "HDD", "model": "WDC WD80EFZZ", "has_other_data": other}


@pytest.fixture
def box(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", str(media))
    updates.write_json(updates.heartbeat_path(), {"at": updates.now_iso(), "script": 5})
    store = RequestStore(str(tmp_path / "test.db"))
    store.save_plex_server("m", "Server", "http://plex.invalid", "t")
    admin = store.upsert_user("admin-id", "admin", True)
    session = store.create_session("s", admin.plex_user_id, admin.username, True,
                                   (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    original = api.app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store
        app.state.qbt = None
        app.state.worker = None
        yield

    api.app.router.lifespan_context = lifespan
    deps.limiter.reset()
    with TestClient(api.app) as client:
        client.cookies.set(deps.SESSION_COOKIE_NAME, session.id)
        yield client, media, store
    api.app.router.lifespan_context = original


def test_an_install_that_isn_t_a_box_has_no_drives_page(box, monkeypatch):
    client, _, _ = box
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", None)
    assert client.get("/api/admin/drives").json() == {"managed": False}
    assert client.post("/api/admin/drives", json={"disk": "sdb", "serial": "x", "confirm": True}).status_code == 404


def test_the_page_lists_each_drive_with_its_space_and_health(box):
    client, media, _ = box
    add_drive(media, "drive1")
    add_drive(media, "drive2", 4 * TB)
    report(unused=[disk()], pools=[
        {"id": "drive1", "status": "ONLINE", "healthy": True},
        {"id": "drive2", "status": "DEGRADED", "healthy": False},
        {"id": "drive3", "status": "UNAVAIL", "healthy": False, "size": 2 * TB},  # pulled out
    ])
    body = client.get("/api/admin/drives").json()
    assert body["managed"] and body["report_fresh"] and not body["pending"]
    rows = {d["id"]: d for d in body["drives"]}
    assert (rows["drive1"]["label"], rows["drive1"]["size"], rows["drive1"]["state"]) == ("Drive 1", 8 * TB, "healthy")
    assert rows["drive1"]["free"] is not None
    assert rows["drive2"]["state"] == "problem"
    assert (rows["drive3"]["state"], rows["drive3"]["used"]) == ("missing", None)
    assert body["unused"] == [disk()]


def test_disks_aren_t_offered_from_a_stale_report(box):
    client, _, _ = box
    report(unused=[disk()], age=600)
    body = client.get("/api/admin/drives").json()
    assert body["unused"] == [] and not body["report_fresh"]


def test_adding_a_drive_leaves_a_private_request_for_the_updater(box):
    client, _, store = box
    report(unused=[disk()])
    response = client.post("/api/admin/drives", json={"disk": "sdb", "serial": "WD-AAA", "confirm": True})
    assert response.status_code == 202
    path = updates.updater_dir() / "drive-request.json"
    assert json.loads(path.read_text())["disk"] == "sdb"
    assert (os.stat(path).st_mode & 0o777) == 0o600
    assert client.get("/api/admin/drives").json()["pending"] is True
    # One at a time.
    again = client.post("/api/admin/drives", json={"disk": "sdb", "serial": "WD-AAA", "confirm": True})
    assert again.status_code == 409
    events = client.get("/api/admin/audit-log").json()["events"]
    assert any(e["event_type"] == "drive_added" for e in events)


@pytest.mark.parametrize("body, status, says", [
    ({"disk": "sdb", "serial": "WD-AAA"}, 422, "erased"),
    ({"disk": "sdb", "serial": "WD-OTHER", "confirm": True}, 409, "isn't free"),
    ({"disk": "sdz", "serial": "WD-AAA", "confirm": True}, 409, "isn't free"),
])
def test_nothing_is_asked_for_without_confirming_the_disk_still_chosen(box, body, status, says):
    client, _, _ = box
    report(unused=[disk()])
    response = client.post("/api/admin/drives", json=body)
    assert response.status_code == status and says in response.json()["detail"]
    assert not (updates.updater_dir() / "drive-request.json").exists()


def test_a_disk_with_another_system_s_data_needs_that_agreed_too(box):
    client, _, _ = box
    report(unused=[disk(other=True)])
    assert client.post("/api/admin/drives", json={"disk": "sdb", "serial": "WD-AAA", "confirm": True}).status_code == 422
    assert client.post("/api/admin/drives", json={"disk": "sdb", "serial": "WD-AAA", "confirm": True,
                                                   "erase_other_data": True}).status_code == 202


def test_without_the_updater_nothing_is_left_waiting(box):
    client, _, _ = box
    report(unused=[disk()])
    updates.heartbeat_path().unlink()
    assert client.post("/api/admin/drives", json={"disk": "sdb", "serial": "WD-AAA", "confirm": True}).status_code == 409


def test_the_box_s_storage_is_all_its_drives_together(box, monkeypatch):
    client, media, _ = box
    assert client.get("/api/storage").json() == {"available": False}  # no drive yet
    add_drive(media, "drive1")
    add_drive(media, "drive2")
    sizes = {"drive1": (100, 40, 60), "drive2": (300, 100, 200)}
    monkeypatch.setattr(drives, "usage", lambda d: dict(zip(("total", "used", "free"), sizes[d.id]), readable=True))
    body = client.get("/api/storage").json()
    assert (body["total_bytes"], body["used_bytes"], body["free_bytes"], body["used_percent"]) == (400, 140, 260, 35.0)
