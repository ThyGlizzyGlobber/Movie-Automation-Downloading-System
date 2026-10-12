"""A box's drive that has failed: listing what this app filed on it, and
asking for all of it again (app/drive_recovery.py, app/api/storage.py)."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import api, box_plex, drive_recovery, drives, updates
from app.api import deps
from app.catalogue.tmdb import TMDBError
from app.core import config
from app.db import RequestStore

TB = 1000**4
MOVIE = "/media/drive2/Movies/Heat (1995) {tmdb-949}/Heat.1995.1080p.BluRay.mkv"


class FakeTMDB:
    def __init__(self, unknown=()):
        self.unknown = set(unknown)

    def get_movie(self, tmdb_id):
        if tmdb_id in self.unknown:
            raise TMDBError("gone")
        return {"title": {949: "Heat", 603: "The Matrix"}.get(tmdb_id, "Film"), "release_date": "1995-12-15",
                "poster_path": "/p.jpg"}

    def get_tv(self, tmdb_id):
        if tmdb_id in self.unknown:
            raise TMDBError("gone")
        return {"name": {1396: "Breaking Bad", 1399: "Game of Thrones"}.get(tmdb_id, "Show"),
                "first_air_date": "2008-01-20", "status": "Ended", "number_of_seasons": 5, "seasons": []}


class FakeWorker:
    def __init__(self):
        self.queued = []

    def enqueue(self, request_id):
        self.queued.append(request_id)


def file(store, path, tmdb_id, media_type, season=None, episode=None, size=TB // 100):
    with store._lock:
        store._conn.execute(
            "INSERT INTO library_items (tmdb_id, media_type, season_number, episode_number, path, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (tmdb_id, media_type, season, episode, path, size, "2026-10-01T00:00:00+00:00"))
        store._conn.commit()


def episode_path(drive, season, episode, show="Breaking Bad (2008) {tmdb-1396}"):
    return f"/media/{drive}/TV Shows/{show}/Season {season:02d}/{show.split(' (')[0]} - s{season:02d}e{episode:02d}.mkv"


@pytest.fixture
def box(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", str(media))
    updates.write_json(updates.heartbeat_path(), {"at": updates.now_iso(), "script": 5})
    store = RequestStore(str(tmp_path / "test.db"))
    server = store.save_plex_server("m", "Server", "http://plex.invalid", "t")
    admin = store.upsert_user("admin-id", "admin", True)
    session = store.create_session("s", admin.plex_user_id, admin.username, True,
                                   (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    # Drive 1 is there; drive 2 the NAS still has a pool for, but can't find.
    for sub in ("downloads/movies", "Movies", "TV Shows"):
        (media / "drive1" / sub).mkdir(parents=True)
    (media / "drive1" / drives.MARKER).write_text(json.dumps({"label": "Drive 1", "size_bytes": 8 * TB}))
    updates.write_json(updates.updater_dir() / "storage.json", {"at": updates.now_iso(), "unused": [], "drives": [
        {"id": "drive1", "status": "ONLINE", "healthy": True},
        {"id": "drive2", "status": "UNAVAIL", "healthy": False, "size": 4 * TB},
    ]})
    # The paths below are /media/…: the fixture's root stands in for it.
    monkeypatch.setattr(drive_recovery, "drive_folder", lambda drive_id: f"/media/{drive_id}")
    monkeypatch.setattr(box_plex, "sync_libraries", lambda *a, **k: [])
    worker, tmdb = FakeWorker(), FakeTMDB()
    original = api.app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store
        app.state.qbt = None
        app.state.worker = worker
        app.state.tmdb = tmdb
        yield

    api.app.router.lifespan_context = lifespan
    deps.limiter.reset()
    with TestClient(api.app) as client:
        client.cookies.set(deps.SESSION_COOKIE_NAME, session.id)
        client.store, client.worker, client.tmdb, client.server = store, worker, tmdb, server
        yield client
    api.app.router.lifespan_context = original


def fill(store):
    file(store, MOVIE, 949, "movie")
    file(store, "/media/drive1/Movies/The Matrix (1999) {tmdb-603}/The.Matrix.mkv", 603, "movie")
    # Season 1 was all on drive 2, from a pack (no episode on the row).
    for e in (1, 2, 3):
        file(store, episode_path("drive2", 1, e), 1396, "pack", season=1)
    # Season 2 is split across the drives.
    file(store, episode_path("drive1", 2, 1), 1396, "episode", 2, 1)
    file(store, episode_path("drive2", 2, 2), 1396, "episode", 2, 2)
    file(store, episode_path("drive2", 2, 3), 1396, "episode", 2, 3)
    # Of season 3, someone only ever asked for one episode.
    file(store, episode_path("drive2", 3, 5), 1396, "episode", 3, 5)


def test_a_missing_drive_says_what_was_on_it(box):
    fill(box.store)
    rows = {d["id"]: d for d in box.get("/api/admin/drives").json()["drives"]}
    assert rows["drive2"]["state"] == "missing" and rows["drive2"]["lost_files"] == 7

    lost = box.get("/api/admin/drives/drive2/lost").json()
    assert lost["files"] == 7 and lost["unknown_files"] == 0
    assert lost["movies"] == [{"tmdb_id": 949, "title": "Heat", "year": 1995, "files": 1}]
    assert lost["shows"] == [{"tmdb_id": 1396, "title": "Breaking Bad", "year": 2008,
                              "seasons": {"1": [1, 2, 3], "2": [2, 3], "3": [5]}, "pack_seasons": [1]}]


@pytest.mark.parametrize("drive_id, status", [("drive1", 409), ("drive9", 404), ("..", 404), ("drive2x", 404)])
def test_only_a_drive_the_box_can_t_find_is_offered(box, drive_id, status):
    fill(box.store)
    assert box.get(f"/api/admin/drives/{drive_id}/lost").status_code == status
    assert box.post(f"/api/admin/drives/{drive_id}/lost").status_code == status
    assert len(box.store.library_items_under("/media/drive2")) == 7


def test_downloading_it_again_asks_for_each_title_the_way_it_was_lost(box):
    store = box.store
    fill(store)
    show = store.create_show(1396, "Breaking Bad", status="watching")
    for s, e in ((1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)):
        done = store.create_episode_request(1396, show.id, "Breaking Bad", s, e)
        store.update_status(done.id, "complete")
        store.add_show_episode(show.id, s, e, done.id)

    body = box.post("/api/admin/drives/drive2/lost").json()
    assert body == {"requested": 5, "joined": 0, "failed": []}

    made = [store.get_request(i) for i in box.worker.queued]
    assert [(r.media_type, r.tmdb_id, r.season_number, r.episode_number) for r in made] == [
        ("movie", 949, None, None),
        ("pack", 1396, 1, None),  # the whole season was there
        ("episode", 1396, 2, 2),  # only part of it
        ("episode", 1396, 2, 3),
        ("episode", 1396, 3, 5),  # not the whole of season 3
    ]
    assert all(store.get_request_for_server(r.id, box.server.id) for r in made)
    # The lost episodes' ledger slots belong to the new requests (or are
    # free for the pack); the one that survived on drive 1 is untouched.
    slots = {(e.season_number, e.episode_number): e.request_id for e in store.list_show_episodes(show.id)}
    assert (1, 1) not in slots and slots[(2, 2)] == made[2].id and slots[(2, 1)] not in box.worker.queued
    # Drive 2 lists nothing more; drive 1 keeps its files.
    assert store.library_items_under("/media/drive2") == []
    assert len(store.library_items_under("/media/drive1")) == 2
    assert store.get_settings()["box_plex_drop"] == ["/media/drive2/Movies", "/media/drive2/TV Shows"]
    assert any(e["event_type"] == "drive_redownloaded" for e in box.get("/api/admin/audit-log").json()["events"])
    # And there's nothing left to ask for twice.
    assert box.post("/api/admin/drives/drive2/lost").status_code == 409


def test_a_title_already_on_its_way_is_joined_not_fetched_twice(box):
    store = box.store
    file(store, MOVIE, 949, "movie")
    live = store.create_request(949, "Heat", 1995, None)
    body = box.post("/api/admin/drives/drive2/lost").json()
    assert body == {"requested": 0, "joined": 1, "failed": []}
    assert box.worker.queued == []
    assert store.get_request_for_server(live.id, box.server.id) is not None


def test_a_title_tmdb_no_longer_knows_is_said_and_kept_to_try_again(box):
    store = box.store
    fill(store)
    box.tmdb.unknown.add(1396)
    body = box.post("/api/admin/drives/drive2/lost").json()
    assert body["requested"] == 1 and body["failed"] == ["Breaking Bad"]
    left = box.get("/api/admin/drives/drive2/lost").json()
    assert left["movies"] == [] and [s["title"] for s in left["shows"]] == ["Breaking Bad"]


def test_a_pack_carried_in_front_of_another_show_is_put_down_to_the_right_one(box):
    # worker.py files a pack's lead episodes as the show before it, under
    # that show's folder, on the pack's own ledger row.
    store = box.store
    file(store, episode_path("drive2", 3, 1, show="Game of Thrones (2011) {tmdb-1399}"), 1396, "pack", season=1)
    lost = box.get("/api/admin/drives/drive2/lost").json()
    assert [(s["tmdb_id"], s["seasons"]) for s in lost["shows"]] == [(1399, {"3": [1]})]
    body = box.post("/api/admin/drives/drive2/lost").json()
    made = store.get_request(box.worker.queued[0])
    assert body["requested"] == 1 and (made.tmdb_id, made.media_type, made.season_number) == (1399, "pack", 3)


def test_an_install_that_isn_t_a_box_has_no_such_thing(box, monkeypatch):
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", None)
    assert box.get("/api/admin/drives/drive2/lost").status_code == 404


def test_with_downloads_not_running_the_requests_wait_queued(box):
    file(box.store, MOVIE, 949, "movie")
    box.app.state.worker = None
    assert box.post("/api/admin/drives/drive2/lost").json()["requested"] == 1
    assert len(box.store.queued_request_ids()) == 1
