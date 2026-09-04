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

    def search_within_provider(self, query, provider_id, region="US"):
        return {"results": self._search_results, "provider_id": provider_id}

    def get_movie(self, tmdb_id):
        if self._raise_on_get_movie:
            from app.tmdb import TMDBError

            raise TMDBError("not found")
        return self._movie

    # -- Stage 4 discover surface --

    def get_available_popular(self, page=1, region="US"):
        return {"results": [MOVIE], "page": page, "total_pages": 500}

    def get_available_trending(self, time_window="week", region="US"):
        return {"results": [MOVIE], "page": 1}

    def get_watch_providers(self, region="US"):
        return {"results": [{"provider_id": 8, "provider_name": "Netflix", "logo_path": "/netflix.png"}]}

    def discover_by_provider(self, provider_id, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 10, "provider_id": provider_id}

    def get_coming_soon(self, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 3}


class FakeQBTClient:
    def __init__(self, torrent_states=None):
        self.deleted: list[tuple[str, bool]] = []
        self._torrent_states = torrent_states or {}

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
        return self._torrent_states.get(torrent_hash)

    def delete_torrent(self, torrent_hash, delete_files=True):
        self.deleted.append((torrent_hash, delete_files))


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


class FakePlexLinker:
    """Stands in for app.plex.PlexLinker: API tests exercise route
    wiring/error-mapping, not the real PIN polling flow (see test_plex.py
    for that)."""

    def __init__(self):
        self.started = 0
        self._status = {"linked": False, "username": None, "server_name": None, "pending": False, "error": None}
        self.raise_on_start: Exception | None = None

    async def start(self) -> str:
        self.started += 1
        if self.raise_on_start:
            raise self.raise_on_start
        return "https://app.plex.tv/auth#?clientID=test&code=ABCD"

    def status(self) -> dict:
        return self._status

    def unlink(self) -> None:
        self._status = {"linked": False, "username": None, "server_name": None, "pending": False, "error": None}


@pytest.fixture
def client_and_deps(tmp_path):
    store = RequestStore(str(tmp_path / "test.db"))
    tmdb = FakeTMDBClient()
    worker = NoOpWorker()
    qbt = FakeQBTClient()
    plex_linker = FakePlexLinker()

    @asynccontextmanager
    async def test_lifespan(app):
        app.state.store = store
        app.state.tmdb = tmdb
        app.state.worker = worker
        app.state.qbt = qbt
        app.state.plex_linker = plex_linker
        yield

    api.app.router.lifespan_context = test_lifespan
    with TestClient(api.app) as client:
        yield client, store, tmdb, worker, qbt, plex_linker


def test_search_returns_tmdb_results(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/search", json={"query": "dune"})

    assert response.status_code == 200
    assert response.json() == [MOVIE]


def test_search_rejects_empty_query(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/search", json={"query": ""})

    assert response.status_code == 422


def test_search_with_provider_id_uses_provider_scoped_search(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    calls = []
    tmdb.search_within_provider = lambda query, provider_id, region="US": (
        calls.append((query, provider_id)) or {"results": [MOVIE]}
    )

    response = client.post("/api/search", json={"query": "dune", "provider_id": 8})

    assert response.status_code == 200
    assert response.json() == [MOVIE]
    assert calls == [("dune", 8)]


def test_create_request_persists_and_enqueues(client_and_deps):
    client, store, _, worker, _, _ = client_and_deps
    response = client.post("/api/requests", json={"tmdb_id": 693134, "query": "dune"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["title"] == "Dune: Part Two"
    assert body["release_year"] == 2024
    assert store.get_request(body["id"]) is not None
    assert worker.enqueued == [body["id"]]


def test_create_request_404s_on_unknown_tmdb_id(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_movie = True

    response = client.post("/api/requests", json={"tmdb_id": 999999})

    assert response.status_code == 404


def test_get_request_returns_full_row(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()

    response = client.get(f"/api/requests/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_request_404s_when_missing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/requests/999999")

    assert response.status_code == 404


def test_list_requests_newest_first_and_status_filter(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    first = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    second = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(second["id"], "failed", error_message="boom")

    all_rows = client.get("/api/requests").json()
    assert [r["id"] for r in all_rows] == [second["id"], first["id"]]

    failed_only = client.get("/api/requests", params={"status": "failed"}).json()
    assert [r["id"] for r in failed_only] == [second["id"]]


def test_cancel_deletes_torrent_and_files_and_marks_cancelled(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "downloading", result={"torrent_hash": "aaaa"})
    qbt._torrent_states["aaaa"] = {"progress": 0.4}

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert qbt.deleted == [("aaaa", True)]
    assert store.get_request(created["id"]).status == "cancelled"


def test_cancel_works_from_complete_status_too(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "complete", result={"torrent_hash": "bbbb"})
    qbt._torrent_states["bbbb"] = {"progress": 1.0}

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 200
    assert qbt.deleted == [("bbbb", True)]


def test_cancel_fails_honestly_when_qbittorrent_already_removed_the_torrent(client_and_deps):
    """qBittorrent's own "remove torrent after completion" setting can
    auto-remove a finished torrent before anyone clicks Cancel. There's no
    file left to delete through qBittorrent's API and no other path to
    it — this must fail loudly rather than mark "cancelled" and imply
    files were removed when nothing was touched."""
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "complete", result={"torrent_hash": "cccc"})
    # "cccc" absent from qbt._torrent_states -> torrent_info returns None

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 409
    assert "auto-removed" in response.json()["detail"]
    assert qbt.deleted == []
    assert store.get_request(created["id"]).status == "complete"


def test_cancel_rejects_a_status_with_no_torrent_yet(client_and_deps):
    client, _, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()  # starts "queued"

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 409
    assert qbt.deleted == []


def test_cancel_rejects_when_hash_was_never_captured(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "downloading", result={"torrent_hash": None})

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 409
    assert qbt.deleted == []


def test_cancel_404s_when_missing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/requests/999999/cancel")

    assert response.status_code == 404


def test_clear_requests_removes_terminal_rows_only(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    terminal = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(terminal["id"], "failed", error_message="boom")
    active = client.post("/api/requests", json={"tmdb_id": 693134}).json()  # stays "queued"

    response = client.post("/api/requests/clear")

    assert response.status_code == 200
    assert response.json() == {"removed": 1}
    assert store.get_request(terminal["id"]) is None
    assert store.get_request(active["id"]) is not None


def test_get_retention_defaults_to_none(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/settings/retention")

    assert response.status_code == 200
    assert response.json() == {"days": None}


def test_set_retention_persists_and_reads_back(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/retention", json={"days": 90})

    assert response.status_code == 200
    assert response.json() == {"days": 90}
    assert store.get_settings()["request_retention_days"] == 90
    assert client.get("/api/settings/retention").json() == {"days": 90}


def test_get_appearance_defaults_to_none(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/settings/appearance")

    assert response.status_code == 200
    assert response.json() == {"accent_color": None}


def test_set_appearance_persists_and_reads_back(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/appearance", json={"accent_color": "#8e24aa"})

    assert response.status_code == 200
    assert response.json() == {"accent_color": "#8e24aa"}
    assert store.get_settings()["accent_color"] == "#8e24aa"
    assert client.get("/api/settings/appearance").json() == {"accent_color": "#8e24aa"}


def test_set_appearance_rejects_a_non_hex_color(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/appearance", json={"accent_color": "blue"})

    assert response.status_code == 422


def test_set_appearance_null_resets_to_default(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    client.put("/api/settings/appearance", json={"accent_color": "#8e24aa"})

    response = client.put("/api/settings/appearance", json={"accent_color": None})

    assert response.status_code == 200
    assert store.get_settings()["accent_color"] is None


def test_plex_status_reflects_linker(client_and_deps):
    client, _, _, _, _, plex_linker = client_and_deps
    plex_linker._status = {"linked": True, "username": "bejay", "server_name": "NAS", "pending": False, "error": None}

    response = client.get("/api/plex/status")

    assert response.status_code == 200
    assert response.json()["username"] == "bejay"


def test_plex_link_start_returns_auth_url(client_and_deps):
    client, _, _, _, _, plex_linker = client_and_deps
    response = client.post("/api/plex/link")

    assert response.status_code == 200
    assert response.json()["auth_url"].startswith("https://app.plex.tv/auth")
    assert plex_linker.started == 1


def test_plex_link_start_maps_plex_error_to_502(client_and_deps):
    from app.plex import PlexError

    client, _, _, _, _, plex_linker = client_and_deps
    plex_linker.raise_on_start = PlexError("plex.tv is down")

    response = client.post("/api/plex/link")

    assert response.status_code == 502


def test_plex_unlink_clears_status(client_and_deps):
    client, _, _, _, _, plex_linker = client_and_deps
    plex_linker._status = {"linked": True, "username": "bejay", "server_name": "NAS", "pending": False, "error": None}

    response = client.post("/api/plex/unlink")

    assert response.status_code == 200
    assert response.json()["linked"] is False


def test_discover_popular_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/popular", params={"page": 2})

    assert response.status_code == 200
    assert response.json()["results"] == [MOVIE]


def test_discover_trending_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/trending")

    assert response.status_code == 200
    assert response.json()["results"] == [MOVIE]


def test_discover_providers_returns_results_list(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/providers")

    assert response.status_code == 200
    assert response.json() == [{"provider_id": 8, "provider_name": "Netflix", "logo_path": "/netflix.png"}]


def test_discover_by_provider_passes_provider_id_through(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/providers/8")

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [MOVIE]
    assert body["provider_id"] == 8


def test_discover_coming_soon_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/coming-soon", params={"page": 2})

    assert response.status_code == 200
    assert response.json()["results"] == [MOVIE]


def test_get_movie_detail_returns_full_movie(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/movies/693134")

    assert response.status_code == 200
    assert response.json() == MOVIE


def test_get_movie_detail_404s_on_unknown_tmdb_id(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_movie = True

    response = client.get("/api/movies/999999")

    assert response.status_code == 404


def test_deploy_runs_git_pull_and_returns_its_result(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api, "run_git_pull", lambda: {"detail": "Already up to date.", "commit": "abc1234"})

    response = client.post("/api/admin/deploy")

    assert response.status_code == 200
    assert response.json() == {"detail": "Already up to date.", "commit": "abc1234"}


def test_deploy_maps_deploy_error_to_502(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps

    def raise_deploy_error():
        raise api.DeployError("not a git clone")

    monkeypatch.setattr(api, "run_git_pull", raise_deploy_error)

    response = client.post("/api/admin/deploy")

    assert response.status_code == 502
    assert response.json()["detail"] == "not a git clone"
