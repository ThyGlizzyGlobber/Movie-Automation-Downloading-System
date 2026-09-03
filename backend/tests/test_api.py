"""API tests with the pipeline's dependencies faked out (TMDB + qBittorrent)
via FastAPI dependency overrides and a lifespan override that skips the
real background worker — see the manual end-to-end run in project.md for
the real-TMDB/real-qBittorrent validation this doesn't cover."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app import api
from app.db import RequestStore

MOVIE = {
    "id": 693134,
    "title": "Dune: Part Two",
    "original_title": "Dune: Part Two",
    "release_date": "2024-03-01",
}


class FakeTMDBClient:
    def __init__(self, search_results=None, movie=None, raise_on_get_movie=False):
        self._search_results = search_results if search_results is not None else [MOVIE]
        self._movie = movie or MOVIE
        self._raise_on_get_movie = raise_on_get_movie

    def search_movie(self, query, year=None):
        return {"results": self._search_results}

    def get_movie(self, tmdb_id):
        if self._raise_on_get_movie:
            from app.tmdb import TMDBError

            raise TMDBError("not found")
        return self._movie


class FakeQBTClient:
    def search(self, pattern, category="movies", plugins="enabled"):
        return []

    def existing_torrent_hashes(self):
        return set()

    def free_space_bytes(self):
        return 1_000_000_000_000

    def ensure_category(self, category):
        pass

    def add_torrent(self, file_url, category):
        pass

    def torrent_info(self, torrent_hash):
        return None


class NoOpWorker:
    """Stands in for app.worker.Worker: records what got enqueued, runs no
    background tasks — API tests exercise routes/persistence, not the
    worker (see test_worker.py for that)."""

    def __init__(self):
        self.enqueued: list[int] = []

    def enqueue(self, request_id: int) -> None:
        self.enqueued.append(request_id)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


@pytest.fixture
def client_and_deps(tmp_path):
    store = RequestStore(str(tmp_path / "test.db"))
    tmdb = FakeTMDBClient()
    worker = NoOpWorker()

    @asynccontextmanager
    async def test_lifespan(app):
        app.state.store = store
        app.state.tmdb = tmdb
        app.state.worker = worker
        yield

    api.app.router.lifespan_context = test_lifespan
    with TestClient(api.app) as client:
        yield client, store, tmdb, worker


def test_search_returns_tmdb_results(client_and_deps):
    client, _, _, _ = client_and_deps
    response = client.post("/api/search", json={"query": "dune"})

    assert response.status_code == 200
    assert response.json() == [MOVIE]


def test_search_rejects_empty_query(client_and_deps):
    client, _, _, _ = client_and_deps
    response = client.post("/api/search", json={"query": ""})

    assert response.status_code == 422


def test_create_request_persists_and_enqueues(client_and_deps):
    client, store, _, worker = client_and_deps
    response = client.post("/api/requests", json={"tmdb_id": 693134, "query": "dune"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["title"] == "Dune: Part Two"
    assert body["release_year"] == 2024
    assert store.get_request(body["id"]) is not None
    assert worker.enqueued == [body["id"]]


def test_create_request_404s_on_unknown_tmdb_id(client_and_deps):
    client, _, tmdb, _ = client_and_deps
    tmdb._raise_on_get_movie = True

    response = client.post("/api/requests", json={"tmdb_id": 999999})

    assert response.status_code == 404


def test_get_request_returns_full_row(client_and_deps):
    client, _, _, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()

    response = client.get(f"/api/requests/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_request_404s_when_missing(client_and_deps):
    client, _, _, _ = client_and_deps
    response = client.get("/api/requests/999999")

    assert response.status_code == 404


def test_list_requests_newest_first_and_status_filter(client_and_deps):
    client, store, _, _ = client_and_deps
    first = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    second = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(second["id"], "failed", error_message="boom")

    all_rows = client.get("/api/requests").json()
    assert [r["id"] for r in all_rows] == [second["id"], first["id"]]

    failed_only = client.get("/api/requests", params={"status": "failed"}).json()
    assert [r["id"] for r in failed_only] == [second["id"]]


def test_deploy_stub_returns_501(client_and_deps):
    client, _, _, _ = client_and_deps
    response = client.post("/api/admin/deploy")

    assert response.status_code == 501
