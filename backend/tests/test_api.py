"""API tests with the pipeline's dependencies faked out (TMDB + qBittorrent)
via FastAPI dependency overrides and a lifespan override that skips the
real background worker — see the manual end-to-end run in project.md for
the real-TMDB/real-qBittorrent validation this doesn't cover."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import api, config
from app.db import RequestStore
from app.plex import PlexClient

MOVIE = {
    "id": 693134,
    "title": "Dune: Part Two",
    "original_title": "Dune: Part Two",
    "release_date": "2024-03-01",
    "poster_path": "/dunepart2.jpg",
}

SHOW = {
    "id": 95350,
    "name": "Lanterns",
    "original_name": "Lanterns",
    "first_air_date": "2026-01-01",
    "status": "Returning Series",
    "number_of_seasons": 1,
    "poster_path": "/lanterns.jpg",
}

PERSON = {
    "id": 8293,
    "name": "Jason Sudeikis",
    "profile_path": "/sudeikis.jpg",
    "known_for_department": "Acting",
    "combined_credits": {
        "cast": [
            {
                "id": 693134,
                "media_type": "movie",
                "title": "Dune: Part Two",
                "release_date": "2024-03-01",
                "poster_path": "/dune.jpg",
                "character": "Someone",
            },
            {
                "id": 97546,
                "media_type": "tv",
                "name": "Ted Lasso",
                "first_air_date": "2020-08-14",
                "poster_path": "/tedlasso.jpg",
                "character": "Ted Lasso",
            },
            # A duplicate (id, media_type) pair — TMDB really does list the
            # same show twice for a recurring role split across guest and
            # regular billing — which get_person_detail's dedup must collapse.
            {
                "id": 97546,
                "media_type": "tv",
                "name": "Ted Lasso",
                "first_air_date": "2020-08-14",
                "poster_path": "/tedlasso.jpg",
                "character": "Ted Lasso (guest)",
            },
        ]
    },
}


class FakeTMDBClient:
    def __init__(
        self,
        search_results=None,
        movie=None,
        raise_on_get_movie=False,
        tv_search_results=None,
        raise_on_get_tv=False,
        person=None,
        raise_on_get_person=False,
        movie_videos=None,
        raise_on_get_movie_videos=False,
        tv_videos=None,
        raise_on_get_tv_videos=False,
    ):
        self._search_results = search_results if search_results is not None else [MOVIE]
        self._movie = movie or MOVIE
        self._raise_on_get_movie = raise_on_get_movie
        self._tv_search_results = tv_search_results if tv_search_results is not None else [SHOW]
        self._raise_on_get_tv = raise_on_get_tv
        self._person = person or PERSON
        self._raise_on_get_person = raise_on_get_person
        self._movie_videos = movie_videos if movie_videos is not None else []
        self._raise_on_get_movie_videos = raise_on_get_movie_videos
        self._tv_videos = tv_videos if tv_videos is not None else []
        self._raise_on_get_tv_videos = raise_on_get_tv_videos

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

    def get_available_trending(self, time_window="week", region="US", page=1):
        return {"results": [MOVIE], "page": page}

    def get_watch_providers(self, region="US"):
        return {"results": [{"provider_id": 8, "provider_name": "Netflix", "logo_path": "/netflix.png"}]}

    def get_available_by_provider(self, provider_id, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 10, "provider_id": provider_id}

    def get_available_by_genre(self, genre_id, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 10, "genre_id": genre_id}

    def get_coming_soon(self, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 3}

    def browse_movies(self, **filters):
        return {"results": [MOVIE], "page": filters.get("page", 1), "total_pages": 10, "filters": filters}

    def browse_tv(self, **filters):
        return {"results": [SHOW], "page": filters.get("page", 1), "total_pages": 10, "filters": filters}

    # -- Stage 12: show subscriptions --

    def get_tv(self, tmdb_id):
        if self._raise_on_get_movie or self._raise_on_get_tv:
            from app.tmdb import TMDBError

            raise TMDBError("not found")
        return dict(SHOW, id=tmdb_id)

    # -- Stage 14: TV browse surface --

    def get_available_tv_popular(self, page=1):
        return {"results": [SHOW], "page": page, "total_pages": 500}

    def get_available_tv_trending(self, time_window="week", page=1):
        return {"results": [SHOW], "page": page}

    def get_tv_coming_soon(self, region="US", page=1):
        return {"results": [SHOW], "page": page, "total_pages": 3}

    def search_tv(self, query, year=None):
        return {"results": self._tv_search_results}

    def search_tv_within_provider(self, query, provider_id, region="US"):
        return {"results": self._tv_search_results, "provider_id": provider_id}

    def get_available_tv_by_provider(self, provider_id, region="US", page=1):
        return {"results": [SHOW], "page": page, "total_pages": 10, "provider_id": provider_id}

    def get_available_tv_by_genre(self, genre_id, region="US", page=1):
        return {"results": [SHOW], "page": page, "total_pages": 10, "genre_id": genre_id}

    def get_tv_season(self, tmdb_id, season_number):
        return []

    # -- person filmography --

    def get_person(self, person_id):
        if self._raise_on_get_person:
            from app.tmdb import TMDBError

            raise TMDBError("not found")
        return dict(self._person, id=person_id)

    # -- trailers --

    def get_movie_videos(self, tmdb_id):
        if self._raise_on_get_movie_videos:
            from app.tmdb import TMDBError

            raise TMDBError("upstream error")
        return self._movie_videos

    def get_tv_videos(self, tmdb_id):
        if self._raise_on_get_tv_videos:
            from app.tmdb import TMDBError

            raise TMDBError("upstream error")
        return self._tv_videos


class FakeQBTClient:
    def __init__(self, torrent_states=None, reachable=True):
        self.deleted: list[tuple[str, bool]] = []
        self._torrent_states = torrent_states or {}
        self._reachable = reachable

    def ping(self):
        return self._reachable

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
        self.checked_shows: list[int] = []
        self.checked_shows_full_backfill: list[bool] = []

    def enqueue(self, request_id: int) -> None:
        self.enqueued.append(request_id)

    def check_show(self, show, full_backfill: bool = False) -> int:
        """Stands in for the real catch-up check api.py's POST /api/shows
        triggers immediately after creating a subscription — records that
        it was called (and with what `full_backfill` value) rather than
        actually hitting TMDB (see test_worker.py for the real
        check_show()/scheduler logic)."""
        self.checked_shows.append(show.id)
        self.checked_shows_full_backfill.append(full_backfill)
        return 0

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


class FakeLoginSession:
    """Stands in for app.plex.LoginSession: API tests exercise route
    wiring, not the real PIN polling flow (see test_plex.py for that).

    Models the per-attempt keying for real, rather than ignoring the
    attempt id and always answering `_status` — the whole point of that
    id is that an unknown one gets nothing, so a fake that waved it
    through would make the routes look correct no matter what they did
    with it."""

    def __init__(self):
        self.started = 0
        self._status = {"pending": False, "result": None, "error": None}
        self.raise_on_start: Exception | None = None
        self.live_attempts: set[str] = set()

    async def start(self) -> tuple[str, str]:
        self.started += 1
        if self.raise_on_start:
            raise self.raise_on_start
        attempt_id = f"test-attempt-{self.started}"
        self.live_attempts.add(attempt_id)
        return attempt_id, "https://app.plex.tv/auth#?clientID=test&code=ABCD"

    def status(self, attempt_id: str | None) -> dict:
        if attempt_id not in self.live_attempts:
            return {"pending": False, "result": None, "error": None}
        return self._status

    def finish(self, attempt_id: str) -> None:
        self.live_attempts.discard(attempt_id)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """slowapi's limiter is in-memory and keyed by client IP, and every
    test here reaches the app as the same TestClient IP — so without a
    reset, budget spent by one test 429s a later one and the file turns
    order-dependent (which it quietly already was: adding a handful of
    tests that call /api/auth/login/start was enough to start knocking
    over unrelated ones). Autouse rather than folded into
    `client_and_deps`, since tests that take no client still share the
    process-wide limiter."""
    api.limiter.reset()
    yield


@pytest.fixture
def client_and_deps(tmp_path):
    store = RequestStore(str(tmp_path / "test.db"))
    tmdb = FakeTMDBClient()
    worker = NoOpWorker()
    qbt = FakeQBTClient()
    plex_linker = FakePlexLinker()
    login_session = FakeLoginSession()

    @asynccontextmanager
    async def test_lifespan(app):
        app.state.store = store
        app.state.tmdb = tmdb
        app.state.worker = worker
        app.state.qbt = qbt
        app.state.plex_linker = plex_linker
        app.state.login_session = login_session
        yield

    api.app.router.lifespan_context = test_lifespan
    with TestClient(api.app) as client:
        # Every test in this file predates auth (frontend migration Part
        # C) and exercises route logic on the assumption that whoever's
        # calling is already allowed to — rather than touch every one of
        # them individually, the fixture itself establishes one signed-in
        # admin session up front, and TestClient (which persists cookies
        # across requests on the same instance) carries it automatically
        # from here on. Tests that specifically need to exercise
        # unauthenticated/non-admin/setup-bootstrap behavior use the
        # separate `unauthenticated_client`/`non_admin_client` fixtures
        # below instead of this one.
        store.update_settings(
            {
                "plex_token": "admin-plex-token",
                "plex_client_id": "test-client-id",
                "plex_server_machine_id": "test-machine-id",
            }
        )
        admin = store.upsert_user("admin-plex-id", "admin", True)
        session = store.create_session(
            "test-admin-session",
            admin.plex_user_id,
            admin.username,
            True,
            (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )
        client.cookies.set(api.SESSION_COOKIE_NAME, session.id)
        yield client, store, tmdb, worker, qbt, plex_linker


@pytest.fixture
def unauthenticated_client(client_and_deps):
    """The same fixture, but with the admin session cookie stripped —
    for tests that specifically exercise the "not signed in" 401 path."""
    client, store, tmdb, worker, qbt, plex_linker = client_and_deps
    client.cookies.delete(api.SESSION_COOKIE_NAME)
    return client, store, tmdb, worker, qbt, plex_linker


@pytest.fixture
def non_admin_client(client_and_deps):
    """The same fixture, but signed in as a regular (non-admin) user —
    for tests that specifically exercise the "signed in, not admin" 403
    path. A distinct Plex identity from the fixture's own admin user."""
    client, store, tmdb, worker, qbt, plex_linker = client_and_deps
    user = store.upsert_user("regular-plex-id", "regular-user", False)
    session = store.create_session(
        "test-regular-session",
        user.plex_user_id,
        user.username,
        False,
        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    client.cookies.set(api.SESSION_COOKIE_NAME, session.id)
    return client, store, tmdb, worker, qbt, plex_linker


def test_search_returns_tmdb_results(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/search", json={"query": "dune"})

    assert response.status_code == 200
    assert response.json() == [dict(MOVIE, on_plex=False)]


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
    assert response.json() == [dict(MOVIE, on_plex=False)]
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


def test_create_request_persists_poster_path(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    response = client.post("/api/requests", json={"tmdb_id": 693134, "query": "dune"})

    assert response.status_code == 201
    body = response.json()
    assert body["poster_path"] == "/dunepart2.jpg"
    assert store.get_request(body["id"]).poster_path == "/dunepart2.jpg"


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


def test_cancel_marks_queued_request_cancelled_without_touching_qbittorrent(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()  # starts "queued"

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert qbt.deleted == []
    assert store.get_request(created["id"]).status == "cancelled"


def test_cancel_rejects_searching_status(client_and_deps):
    """"searching" is deliberately not cancellable — the pipeline is
    actively running with no cancellation hook, unlike "queued" (nothing
    started yet) or "downloading"/"complete" (a real torrent to delete)."""
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "searching")

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 409
    assert qbt.deleted == []


def test_cancel_stops_tracking_when_hash_was_never_captured(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "downloading", result={"torrent_hash": None})

    response = client.post(f"/api/requests/{created['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert qbt.deleted == []


def test_cancel_rejects_a_complete_row_with_no_hash(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "complete", result={"torrent_hash": None})

    assert client.post(f"/api/requests/{created['id']}/cancel").status_code == 409
    assert qbt.deleted == []


def test_cancel_404s_when_missing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/requests/999999/cancel")

    assert response.status_code == 404


def test_reject_deletes_torrent_and_blacklists_hash_for_the_movie(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "downloading", result={"torrent_hash": "aaaa"})
    qbt._torrent_states["aaaa"] = {"progress": 0.4}

    response = client.post(f"/api/requests/{created['id']}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert qbt.deleted == [("aaaa", True)]
    assert store.get_request(created["id"]).status == "cancelled"
    assert store.get_rejected_torrent_hashes(693134) == {"aaaa"}


def test_reject_by_request_also_blacklists_the_release_name_and_size(client_and_deps):
    """A hash only rules out magnet listings; the winner's name and size
    rule it out as a direct .torrent link too (Mutiny, live 2026-09-17)."""
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(
        created["id"],
        "downloading",
        result={"torrent_hash": "aaaa", "winner": {"fileName": "Mutiny.2026.2160p.REMUX.mkv", "fileSize": 51_000_000_000, "fileUrl": "https://tracker/x.torrent"}},
    )
    qbt._torrent_states["aaaa"] = {"progress": 0.4}

    assert client.post(f"/api/requests/{created['id']}/reject").status_code == 200
    assert store.get_rejected_torrent_hashes(693134) == {"aaaa"}
    assert store.get_rejected_releases(693134) == [{"name": "Mutiny.2026.2160p.REMUX.mkv", "size_bytes": None}]


def test_reject_works_from_complete_status_too(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "complete", result={"torrent_hash": "bbbb"})
    qbt._torrent_states["bbbb"] = {"progress": 1.0}

    response = client.post(f"/api/requests/{created['id']}/reject")

    assert response.status_code == 200
    assert qbt.deleted == [("bbbb", True)]
    assert store.get_rejected_torrent_hashes(693134) == {"bbbb"}


def test_reject_rejects_queued_status(client_and_deps):
    """A "queued" request has no torrent on record yet, so there's nothing
    to blacklist — unlike `cancel`, which just marks it cancelled directly,
    `reject` refuses outright."""
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()  # starts "queued"

    response = client.post(f"/api/requests/{created['id']}/reject")

    assert response.status_code == 409
    assert qbt.deleted == []
    assert store.get_request(created["id"]).status == "queued"
    assert store.get_rejected_torrent_hashes(693134) == set()


def test_reject_still_blacklists_and_removes_the_file_when_the_torrent_is_gone(client_and_deps, tmp_path):
    """A broken copy is usually noticed after the torrent finished and was
    auto-removed (Mutiny, live 2026-09-17). Rejecting then must still
    blacklist the hash and delete what this app filed, or the next search
    picks the same copy again."""
    client, store, _, _, qbt, _ = client_and_deps
    filed = tmp_path / "Mutiny (2026).mkv"
    filed.write_bytes(b"broken")
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "complete", result={"torrent_hash": "cccc", "organized_paths": [str(filed)]})
    # "cccc" absent from qbt._torrent_states -> torrent_info returns None

    response = client.post(f"/api/requests/{created['id']}/reject")

    assert response.status_code == 200
    assert qbt.deleted == []
    assert not filed.exists()
    assert store.get_request(created["id"]).status == "cancelled"
    assert store.get_rejected_torrent_hashes(693134) == {"cccc"}


def test_reject_rejects_when_hash_was_never_captured(client_and_deps):
    client, store, _, _, qbt, _ = client_and_deps
    created = client.post("/api/requests", json={"tmdb_id": 693134}).json()
    store.update_status(created["id"], "downloading", result={"torrent_hash": None})

    response = client.post(f"/api/requests/{created['id']}/reject")

    assert response.status_code == 409
    assert qbt.deleted == []
    assert store.get_rejected_torrent_hashes(693134) == set()


def test_reject_404s_when_missing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/requests/999999/reject")

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


def test_get_pipeline_settings_defaults_match_config(client_and_deps):
    from app import config

    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/settings/pipeline")

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == config.CATEGORY
    assert body["min_resolution"] == config.MIN_RESOLUTION
    assert body["min_size_gb"] == config.MIN_SIZE_GB
    assert body["max_size_gb"] == config.MAX_SIZE_GB
    assert body["language_allowlist"] == list(config.LANGUAGE_ALLOWLIST)
    assert body["language_blocklist"] == list(config.LANGUAGE_BLOCKLIST)
    assert body["language_required"] == list(config.LANGUAGE_REQUIRED)


def test_set_pipeline_settings_persists_and_reads_back(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    response = client.put(
        "/api/settings/pipeline",
        json={
            "category": "family-movies",
            "min_resolution": "1080p",
            "min_size_gb": 2,
            "max_size_gb": 80,
            "language_allowlist": ["english"],
            "language_blocklist": ["french"],
            "language_required": ["english", "spanish"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "category": "family-movies",
        "min_resolution": "1080p",
        "min_size_gb": 2,
        "max_size_gb": 80,
        "language_allowlist": ["english"],
        "language_blocklist": ["french"],
        "language_required": ["english", "spanish"],
    }
    assert store.get_settings()["min_resolution"] == "1080p"
    assert client.get("/api/settings/pipeline").json()["category"] == "family-movies"


def test_set_pipeline_settings_null_fields_reset_to_default(client_and_deps):
    from app import config

    client, _, _, _, _, _ = client_and_deps
    client.put("/api/settings/pipeline", json={"min_resolution": "720p"})

    response = client.put("/api/settings/pipeline", json={"min_resolution": None})

    assert response.status_code == 200
    assert response.json()["min_resolution"] == config.MIN_RESOLUTION


def test_set_pipeline_settings_rejects_unrecognized_resolution(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/pipeline", json={"min_resolution": "8k"})

    assert response.status_code == 422


def test_set_pipeline_settings_rejects_min_size_not_less_than_max(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/pipeline", json={"min_size_gb": 100, "max_size_gb": 50})

    assert response.status_code == 422


def test_set_pipeline_settings_rejects_blank_category(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/pipeline", json={"category": ""})

    assert response.status_code == 422


def test_set_pipeline_settings_rejects_a_partial_edit_that_would_invert_the_effective_range(client_and_deps):
    """The frontend always PUTs the full form state (a null field means
    "reset to default", same convention as RetentionSettings), but the
    endpoint itself doesn't assume that — an
    omitted field reverts to config.py's default, and this must be
    rejected if that reversion would invert the *effective* range, not
    just checked against whatever the request happened to mention."""
    client, store, _, _, _, _ = client_and_deps
    client.put("/api/settings/pipeline", json={"max_size_gb": 100})

    # Sends only min_size_gb — max_size_gb is absent from this request, so
    # it would silently revert to config.py's default (150) if the write
    # went through unchecked (100 < 150, so this specific edit wouldn't
    # actually invert anything — the point is the check still runs against
    # the *effective* max, not skip validation just because max_size_gb
    # wasn't in this particular request).
    response = client.put("/api/settings/pipeline", json={"min_size_gb": 200})

    assert response.status_code == 422
    assert store.get_settings().get("min_size_gb") is None  # rejected before the write


# ---------------------------------------------------------------------------
# /api/settings/tv — Stage 12.x show-check interval & episode auto-recheck
# ---------------------------------------------------------------------------


def test_get_tv_settings_defaults_match_config(client_and_deps):
    from app import config

    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/settings/tv")

    assert response.status_code == 200
    body = response.json()
    assert body["show_check_interval_hours"] == config.SHOW_CHECK_INTERVAL_HOURS
    assert body["episode_recheck_enabled"] == config.EPISODE_RECHECK_ENABLED
    assert body["episode_recheck_interval_hours"] == config.EPISODE_RECHECK_INTERVAL_HOURS
    assert body["episode_recheck_max_attempts"] == config.EPISODE_RECHECK_MAX_ATTEMPTS
    assert body["episode_air_buffer_hours"] == config.EPISODE_AIR_BUFFER_HOURS


def test_set_tv_settings_persists_and_reads_back(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    response = client.put(
        "/api/settings/tv",
        json={
            "show_check_interval_hours": 2,
            "episode_recheck_enabled": True,
            "episode_recheck_interval_hours": 0.5,
            "episode_recheck_max_attempts": 0,
            "episode_air_buffer_hours": 24,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "show_check_interval_hours": 2,
        "episode_recheck_enabled": True,
        "episode_recheck_interval_hours": 0.5,
        "episode_recheck_max_attempts": 0,
        "episode_air_buffer_hours": 24,
    }
    assert store.get_settings()["episode_recheck_enabled"] is True
    assert client.get("/api/settings/tv").json()["show_check_interval_hours"] == 2


def test_set_tv_settings_null_fields_reset_to_default(client_and_deps):
    from app import config

    client, _, _, _, _, _ = client_and_deps
    client.put("/api/settings/tv", json={"episode_recheck_enabled": True})

    response = client.put("/api/settings/tv", json={"episode_recheck_enabled": None})

    assert response.status_code == 200
    assert response.json()["episode_recheck_enabled"] == config.EPISODE_RECHECK_ENABLED


def test_set_tv_settings_accepts_zero_max_attempts_as_infinite(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/tv", json={"episode_recheck_max_attempts": 0})

    assert response.status_code == 200
    assert response.json()["episode_recheck_max_attempts"] == 0


def test_set_tv_settings_rejects_negative_max_attempts(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/tv", json={"episode_recheck_max_attempts": -1})

    assert response.status_code == 422


def test_set_tv_settings_rejects_non_positive_interval(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/tv", json={"show_check_interval_hours": 0})

    assert response.status_code == 422


def test_set_tv_settings_accepts_zero_air_buffer(client_and_deps):
    """0 is a valid, deliberate value (search the instant the air_date
    arrives, the pre-buffer behavior) — distinct from negative, which is
    the actual invalid case."""
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/tv", json={"episode_air_buffer_hours": 0})

    assert response.status_code == 200
    assert response.json()["episode_air_buffer_hours"] == 0


def test_set_tv_settings_rejects_negative_air_buffer(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.put("/api/settings/tv", json={"episode_air_buffer_hours": -1})

    assert response.status_code == 422


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
    assert response.json()["results"] == [dict(MOVIE, on_plex=False)]


def test_discover_trending_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/trending")

    assert response.status_code == 200
    assert response.json()["results"] == [dict(MOVIE, on_plex=False)]


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
    assert body["results"] == [dict(MOVIE, on_plex=False)]
    assert body["provider_id"] == 8


def test_discover_by_genre_passes_genre_id_through(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/genre/28")

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [dict(MOVIE, on_plex=False)]
    assert body["genre_id"] == 28


def test_discover_coming_soon_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover/coming-soon", params={"page": 2})

    assert response.status_code == 200
    assert response.json()["results"] == [dict(MOVIE, on_plex=False)]


def test_get_movie_detail_returns_full_movie(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/movies/693134")

    assert response.status_code == 200
    # MOVIE's release_date (2024-03-01) is well outside the Coming Soon
    # recency window, so is_coming_soon is deterministically False here
    # regardless of no release_dates data being present on the fixture.
    # on_plex_tracked is False — no completed, organized request exists
    # for this tmdb_id in a fresh store.
    assert response.json() == dict(MOVIE, on_plex=False, plex_file_available=False, is_coming_soon=False, on_plex_tracked=False, logo_path=None)


def test_get_movie_detail_on_plex_tracked_true_after_a_completed_organized_request(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.mark_organized(row.id, ["/library/Dune Part Two (2024)/file.mkv"], [], "2000-01-01T00:00:00+00:00")

    response = client.get("/api/movies/693134")

    assert response.json()["on_plex_tracked"] is True


def test_get_movie_detail_on_plex_tracked_false_for_a_complete_request_with_no_organized_paths(client_and_deps):
    """A request can reach 'complete' without ever going through
    mark_organized (e.g. test fixtures, or a future code path) — only a
    genuine organized_paths record counts."""
    client, store, _, _, _, _ = client_and_deps
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "complete")

    response = client.get("/api/movies/693134")

    assert response.json()["on_plex_tracked"] is False


def test_create_request_overwrite_rejected_without_a_prior_organized_request(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    response = client.post("/api/requests", json={"tmdb_id": 693134, "redownload_mode": "overwrite"})

    assert response.status_code == 400


def test_create_request_overwrite_accepted_with_a_prior_organized_request(client_and_deps):
    client, store, _, worker, _, _ = client_and_deps
    prior = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.mark_organized(prior.id, ["/library/Dune Part Two (2024)/file.mkv"], [], "2000-01-01T00:00:00+00:00")

    response = client.post("/api/requests", json={"tmdb_id": 693134, "redownload_mode": "overwrite"})

    assert response.status_code == 201
    assert response.json()["redownload_mode"] == "overwrite"
    assert worker.enqueued == [response.json()["id"]]


def test_create_request_upgrade_mode_needs_no_prior_organized_request(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    response = client.post("/api/requests", json={"tmdb_id": 693134, "redownload_mode": "upgrade"})

    assert response.status_code == 201
    assert response.json()["redownload_mode"] == "upgrade"


def test_create_request_rejects_unknown_redownload_mode(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    response = client.post("/api/requests", json={"tmdb_id": 693134, "redownload_mode": "delete-everything"})

    assert response.status_code == 422


def test_get_movie_detail_flags_is_coming_soon_for_a_theatrical_only_release(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    tmdb._movie = {
        **MOVIE,
        "release_date": recent,
        "release_dates": {
            "results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": f"{recent}T00:00:00.000Z"}]}]
        },
    }

    response = client.get("/api/movies/693134")

    assert response.status_code == 200
    assert response.json()["is_coming_soon"] is True


def test_get_movie_detail_404s_on_unknown_tmdb_id(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_movie = True

    response = client.get("/api/movies/999999")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /api/person/{person_id} — a cast member's filmography
# ---------------------------------------------------------------------------


def test_get_person_detail_returns_name_and_credits(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/person/8293")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Jason Sudeikis"
    assert body["profile_path"] == "/sudeikis.jpg"
    # Three raw cast entries in, two distinct (id, media_type) pairs out.
    assert len(body["credits"]) == 2
    assert {c["id"] for c in body["credits"]} == {693134, 97546}


def test_get_person_detail_dedupes_repeated_title_and_sorts_newest_first(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/person/8293")

    credits = response.json()["credits"]
    # Dune: Part Two (2024) is newer than Ted Lasso (2020) — and Ted Lasso's
    # duplicate guest-billing entry must have been collapsed away entirely.
    assert [c["id"] for c in credits] == [693134, 97546]
    assert len([c for c in credits if c["id"] == 97546]) == 1


def test_get_person_detail_404s_on_unknown_person_id(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_person = True

    response = client.get("/api/person/999999")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /api/movies/{id}/trailer and /api/tv/{id}/trailer — home hero carousel
# ---------------------------------------------------------------------------


def test_get_movie_trailer_returns_cached_file_url(client_and_deps, monkeypatch, tmp_path):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._movie_videos = [{"site": "YouTube", "type": "Trailer", "official": True, "key": "abc123"}]
    monkeypatch.setattr(
        api.trailers, "ensure_downloaded", lambda media_type, tmdb_id, key: tmp_path / f"{media_type}-{tmdb_id}-{key}.mp4"
    )

    response = client.get("/api/movies/693134/trailer")

    assert response.status_code == 200
    assert response.json() == {"url": "/api/trailers/movie-693134-abc123.mp4"}


def test_get_movie_trailer_returns_null_url_when_none_found(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/movies/693134/trailer")

    assert response.status_code == 200
    assert response.json() == {"url": None}


def test_get_movie_trailer_returns_null_url_when_download_fails(client_and_deps, monkeypatch):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._movie_videos = [{"site": "YouTube", "type": "Trailer", "official": True, "key": "abc123"}]
    monkeypatch.setattr(api.trailers, "ensure_downloaded", lambda media_type, tmdb_id, key: None)

    response = client.get("/api/movies/693134/trailer")

    assert response.status_code == 200
    assert response.json() == {"url": None}


def test_get_movie_trailer_502s_on_upstream_error(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_movie_videos = True

    response = client.get("/api/movies/693134/trailer")

    assert response.status_code == 502


def test_get_tv_trailer_returns_cached_file_url(client_and_deps, monkeypatch, tmp_path):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._tv_videos = [{"site": "YouTube", "type": "Teaser", "official": True, "key": "xyz789"}]
    monkeypatch.setattr(
        api.trailers, "ensure_downloaded", lambda media_type, tmdb_id, key: tmp_path / f"{media_type}-{tmdb_id}-{key}.mp4"
    )

    response = client.get("/api/tv/97546/trailer")

    assert response.status_code == 200
    assert response.json() == {"url": "/api/trailers/tv-97546-xyz789.mp4"}


def test_get_tv_trailer_502s_on_upstream_error(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_tv_videos = True

    response = client.get("/api/tv/97546/trailer")

    assert response.status_code == 502


# ---------------------------------------------------------------------------
# /api/trailers/{filename} — serves a cached hero trailer file
# ---------------------------------------------------------------------------


def test_get_trailer_file_serves_cached_file(client_and_deps, monkeypatch, tmp_path):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api.config, "TRAILER_CACHE_DIR", tmp_path)
    (tmp_path / "movie-693134-abc123.mp4").write_bytes(b"fake video bytes")

    response = client.get("/api/trailers/movie-693134-abc123.mp4")

    assert response.status_code == 200
    assert response.content == b"fake video bytes"
    assert response.headers["content-type"] == "video/mp4"


def test_get_trailer_file_404s_when_missing(client_and_deps, monkeypatch, tmp_path):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api.config, "TRAILER_CACHE_DIR", tmp_path)

    response = client.get("/api/trailers/movie-693134-abc123.mp4")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "filename",
    [
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "movie-693134-abc123.txt",
        "movie-693134-abc123.mp4.exe",
        "not-a-valid-name.mp4",
    ],
)
def test_get_trailer_file_rejects_non_matching_filenames(client_and_deps, monkeypatch, tmp_path, filename):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api.config, "TRAILER_CACHE_DIR", tmp_path)

    response = client.get(f"/api/trailers/{filename}")

    assert response.status_code == 404


def test_tv_discover_coming_soon_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/tv/discover/coming-soon", params={"page": 2})

    assert response.status_code == 200
    assert response.json()["results"] == [dict(SHOW, on_plex=False)]


def test_tv_discover_popular_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/tv/discover/popular", params={"page": 2})

    assert response.status_code == 200
    assert response.json()["results"] == [dict(SHOW, on_plex=False)]
    assert response.json()["page"] == 2


def test_tv_discover_trending_passes_through_tmdb(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/tv/discover/trending")

    assert response.status_code == 200
    assert response.json()["results"] == [dict(SHOW, on_plex=False)]


def test_get_tv_detail_returns_full_show(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/tv/95350")

    assert response.status_code == 200
    # SHOW's first_air_date (2026-01-01) is in the past, so is_coming_soon
    # is deterministically False. on_plex_tracked is False — no completed,
    # organized episode/pack request exists for this tmdb_id in a fresh
    # store.
    assert response.json() == dict(
        SHOW,
        on_plex=False,
        plex_complete=False,
        is_coming_soon=False,
        on_plex_tracked=False,
        logo_path=None,
        # Nothing filed for this show, so the show-level "On disk" tiles
        # have nothing to render.
        library={"files": 0, "total_bytes": 0, "releases": []},
    )


def test_aired_episode_count_counts_whole_earlier_seasons_plus_the_last_aired_episode():
    from app.api import _aired_episode_count

    show = {
        "seasons": [
            {"season_number": 0, "episode_count": 3},
            {"season_number": 1, "episode_count": 8},
            {"season_number": 2, "episode_count": 10},
            {"season_number": 3, "episode_count": 8},
        ],
        "last_episode_to_air": {"season_number": 3, "episode_number": 5},
    }
    assert _aired_episode_count(show) == 8 + 10 + 5
    assert _aired_episode_count({"seasons": []}) == 0


def test_get_tv_detail_plex_complete_when_plex_holds_every_aired_episode(client_and_deps, monkeypatch):
    from app import api

    client, _, tmdb, _, _, _ = client_and_deps
    tmdb.get_tv = lambda tmdb_id: dict(
        SHOW,
        id=tmdb_id,
        seasons=[{"season_number": 1, "episode_count": 8}],
        last_episode_to_air={"season_number": 1, "episode_number": 8},
    )
    monkeypatch.setattr(api, "_on_plex_for", lambda title, year, media_type, store, tmdb_id=None: True)

    monkeypatch.setattr(api, "_plex_episode_count", lambda store, title, year, tmdb_id=None: 8)
    assert client.get("/api/tv/95350").json()["plex_complete"] is True

    monkeypatch.setattr(api, "_plex_episode_count", lambda store, title, year, tmdb_id=None: 6)
    assert client.get("/api/tv/95350").json()["plex_complete"] is False

    monkeypatch.setattr(api, "_plex_episode_count", lambda store, title, year, tmdb_id=None: None)
    assert client.get("/api/tv/95350").json()["plex_complete"] is False


def test_get_tv_detail_on_plex_tracked_true_after_a_completed_organized_pack(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.mark_organized(row.id, ["/library/Lanterns/Season 01/e01.mkv"], [], "2000-01-01T00:00:00+00:00")

    response = client.get("/api/tv/95350")

    assert response.json()["on_plex_tracked"] is True


def test_get_tv_detail_flags_is_coming_soon_for_a_future_first_air_date(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    tmdb.get_tv = lambda tmdb_id: {**SHOW, "id": tmdb_id, "first_air_date": future}

    response = client.get("/api/tv/95350")

    assert response.status_code == 200
    assert response.json()["is_coming_soon"] is True


def test_get_tv_detail_404s_on_unknown_tmdb_id(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_tv = True

    response = client.get("/api/tv/999999")

    assert response.status_code == 404


def test_search_tv_returns_tmdb_results(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/tv/search", json={"query": "lanterns"})

    assert response.status_code == 200
    assert response.json() == [dict(SHOW, on_plex=False)]


def test_search_tv_with_provider_id_uses_provider_scoped_search(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    calls = []
    tmdb.search_tv_within_provider = lambda query, provider_id, region="US": (
        calls.append((query, provider_id)) or {"results": [SHOW]}
    )

    response = client.post("/api/tv/search", json={"query": "lanterns", "provider_id": 8})

    assert response.status_code == 200
    assert response.json() == [dict(SHOW, on_plex=False)]
    assert calls == [("lanterns", 8)]


def test_tv_discover_by_provider_passes_provider_id_through(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/tv/discover/providers/8")

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [dict(SHOW, on_plex=False)]
    assert body["provider_id"] == 8


def test_tv_discover_by_genre_passes_genre_id_through(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/tv/discover/genre/35")

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [dict(SHOW, on_plex=False)]
    assert body["genre_id"] == 35


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


# ---------------------------------------------------------------------------
# Stage 8: health + admin/jobs
# ---------------------------------------------------------------------------


def test_health_reports_qbittorrent_reachable(client_and_deps):
    client, _, _, _, qbt, _ = client_and_deps

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "qbittorrent": True}


def test_health_reports_qbittorrent_unreachable_without_failing(client_and_deps):
    client, _, _, _, qbt, _ = client_and_deps
    qbt._reachable = False

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "qbittorrent": False}


# ---------------------------------------------------------------------------
# /api/storage — always-200 disk-usage indicator, same fail-safe convention
# as /api/health.
# ---------------------------------------------------------------------------


def test_get_storage_unavailable_when_library_root_is_not_a_real_mount(client_and_deps):
    # TV_LIBRARY_ROOT defaults to a plainly-fake path in every environment
    # that doesn't run the real NAS mount (see config.py) — exactly the
    # "no real mount here" case this endpoint has to degrade gracefully for.
    client, _, _, _, _, _ = client_and_deps

    response = client.get("/api/storage")

    assert response.status_code == 200
    assert response.json() == {"available": False}


def test_get_storage_reports_real_usage_when_the_path_exists(client_and_deps, monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    client, _, _, _, _, _ = client_and_deps

    response = client.get("/api/storage")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["total_bytes"] > 0
    assert 0 <= body["used_bytes"] <= body["total_bytes"]
    assert 0 <= body["free_bytes"] <= body["total_bytes"]
    assert 0 <= body["used_percent"] <= 100


def test_admin_jobs_defaults_to_every_failure_shaped_status(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    failed = store.create_request(tmdb_id=1, title="Failed", release_year=2020, query=None)
    store.update_status(failed.id, "failed", error_message="boom")
    no_match = store.create_request(tmdb_id=2, title="No Match", release_year=2020, query=None)
    store.update_status(no_match.id, "no qualifying results")
    no_space = store.create_request(tmdb_id=3, title="No Space", release_year=2020, query=None)
    store.update_status(no_space.id, "insufficient free space")
    done = store.create_request(tmdb_id=4, title="Done", release_year=2020, query=None)
    store.update_status(done.id, "complete")

    response = client.get("/api/admin/jobs")

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert set(ids) == {failed.id, no_match.id, no_space.id}
    assert done.id not in ids


def test_admin_jobs_filters_to_one_status(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    failed = store.create_request(tmdb_id=1, title="Failed", release_year=2020, query=None)
    store.update_status(failed.id, "failed", error_message="boom")
    no_match = store.create_request(tmdb_id=2, title="No Match", release_year=2020, query=None)
    store.update_status(no_match.id, "no qualifying results")

    response = client.get("/api/admin/jobs", params={"status": "failed"})

    ids = [row["id"] for row in response.json()]
    assert ids == [failed.id]


# ---------------------------------------------------------------------------
# /api/admin/activity — frontend migration Part D, Settings' Activity
# Dashboard: every request (not just failure-shaped ones), attributed,
# paginated, plus simple per-user aggregate counts.
# ---------------------------------------------------------------------------


def test_admin_activity_requires_admin(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.get("/api/admin/activity")

    assert response.status_code == 403


def test_admin_activity_returns_every_request_newest_first(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    first = store.create_request(tmdb_id=1, title="First", release_year=2020, query=None)
    store.update_status(first.id, "complete")
    second = store.create_request(tmdb_id=2, title="Second", release_year=2021, query=None)
    store.update_status(second.id, "failed", error_message="boom")

    response = client.get("/api/admin/activity")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [r["id"] for r in body["requests"]] == [second.id, first.id]


def test_admin_activity_paginates_with_limit_and_offset(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    ids = []
    for i in range(5):
        row = store.create_request(tmdb_id=i, title=f"Movie {i}", release_year=2020, query=None)
        ids.append(row.id)

    response = client.get("/api/admin/activity", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert [r["id"] for r in body["requests"]] == list(reversed(ids))[1:3]


def test_admin_activity_aggregates_per_user_stats_from_most_recent_username(client_and_deps):
    """requested_by_username can change between one request and the next
    (it's denormalized per-row, same as title) — the aggregate row must
    reflect that user's *most recent* display name, not an arbitrary one."""
    client, store, _, _, _, _ = client_and_deps
    store.create_request(
        tmdb_id=1, title="A", release_year=2020, query=None,
        requested_by_plex_id="user-1", requested_by_username="OldName",
    )
    store.create_request(
        tmdb_id=2, title="B", release_year=2020, query=None,
        requested_by_plex_id="user-1", requested_by_username="NewName",
    )
    store.create_request(
        tmdb_id=3, title="C", release_year=2020, query=None,
        requested_by_plex_id="user-2", requested_by_username="OtherUser",
    )
    # Worker-created rows (no requester) must not show up as a phantom user.
    store.create_episode_request(tmdb_id=9, show_id=1, title="Show", season_number=1, episode_number=1)

    response = client.get("/api/admin/activity")

    assert response.status_code == 200
    stats = {s["plex_user_id"]: s for s in response.json()["user_stats"]}
    assert set(stats) == {"user-1", "user-2"}
    assert stats["user-1"]["username"] == "NewName"
    assert stats["user-1"]["total_requests"] == 2
    assert stats["user-1"]["requests_this_month"] == 2
    assert stats["user-2"]["username"] == "OtherUser"
    assert stats["user-2"]["total_requests"] == 1


# ---------------------------------------------------------------------------
# Settings' Connections panel (frontend migration Part I) — ongoing,
# admin-gated equivalent of the setup wizard's TMDB/qBittorrent steps.
# ---------------------------------------------------------------------------


def test_update_tmdb_settings_requires_admin(non_admin_client, monkeypatch):
    client, _, _, _, _, _ = non_admin_client
    monkeypatch.setattr(config, "TMDB_API_KEY", None)

    response = client.put("/api/settings/tmdb", json={"api_key": "new-key"})

    assert response.status_code == 403


def test_update_tmdb_settings_persists_when_not_env_configured(client_and_deps, monkeypatch):
    client, store, _, _, _, _ = client_and_deps
    monkeypatch.setattr(config, "TMDB_API_KEY", None)

    response = client.put("/api/settings/tmdb", json={"api_key": "from-settings-ui"})

    assert response.status_code == 200
    body = response.json()
    assert body == {"tmdb_configured": True, "tmdb_source": "db", "restart_required": True}
    assert store.get_settings()["tmdb_api_key"] == "from-settings-ui"


def test_update_tmdb_settings_refuses_to_override_an_env_configured_key(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(config, "TMDB_API_KEY", "from-env")

    response = client.put("/api/settings/tmdb", json={"api_key": "from-ui"})

    assert response.status_code == 409


def test_qbt_settings_test_connection_requires_admin(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.post(
        "/api/settings/qbittorrent/test", json={"host": "1.2.3.4", "port": 8080, "username": "", "password": ""}
    )

    assert response.status_code == 403


def test_qbt_settings_test_connection_reports_reachability(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps

    class FakePingClient:
        def __init__(self, host, port, username, password):
            pass

        def ping(self):
            return True

    monkeypatch.setattr(api, "QBTClient", FakePingClient)

    response = client.post(
        "/api/settings/qbittorrent/test", json={"host": "1.2.3.4", "port": 8080, "username": "", "password": ""}
    )

    assert response.status_code == 200
    assert response.json() == {"reachable": True}


def test_update_qbt_settings_persists_when_not_env_configured(client_and_deps, monkeypatch):
    client, store, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api.config, "_QBIT_HOST_ENV", None)
    monkeypatch.setattr(api.config, "_QBIT_PORT_ENV", None)
    monkeypatch.setattr(api.config, "_QBIT_USERNAME_ENV", None)
    monkeypatch.setattr(api.config, "_QBIT_PASSWORD_ENV", None)

    response = client.put(
        "/api/settings/qbittorrent", json={"host": "10.0.0.5", "port": 9090, "username": "me", "password": "pw"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"qbt_configured": True, "qbt_source": "db", "restart_required": True}
    settings = store.get_settings()
    assert settings["qbt_host"] == "10.0.0.5"
    assert settings["qbt_port"] == 9090


def test_update_qbt_settings_refuses_to_override_env_configuration(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api.config, "_QBIT_HOST_ENV", "10.0.0.1")

    response = client.put(
        "/api/settings/qbittorrent", json={"host": "10.0.0.5", "port": 9090, "username": "", "password": ""}
    )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# /api/shows — Stage 12 standing subscriptions
# ---------------------------------------------------------------------------


def test_create_show_subscribes_and_runs_immediate_catchup(client_and_deps):
    client, store, _, worker, _, _ = client_and_deps

    response = client.post("/api/shows", json={"tmdb_id": 95350})

    assert response.status_code == 201
    body = response.json()
    assert body["tmdb_id"] == 95350
    assert body["title"] == "Lanterns"
    assert body["status"] == "watching"
    assert body["last_checked_at"] is None  # NoOpWorker.check_show doesn't touch it
    assert worker.checked_shows == [body["id"]]
    # Stage 14.x: the immediate post-subscribe catch-up must be a full
    # backfill (every season, not just the latest) — a show with nothing
    # downloaded at all otherwise silently skipped everything before its
    # current season.
    assert worker.checked_shows_full_backfill == [True]
    assert store.get_show(body["id"]) is not None


def test_create_show_persists_poster_path(client_and_deps):
    client, store, _, _, _, _ = client_and_deps

    response = client.post("/api/shows", json={"tmdb_id": 95350})

    assert response.status_code == 201
    body = response.json()
    assert body["poster_path"] == "/lanterns.jpg"
    assert store.get_show(body["id"]).poster_path == "/lanterns.jpg"


def test_create_show_404s_on_unknown_tmdb_id(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_movie = True

    response = client.post("/api/shows", json={"tmdb_id": 999999})

    assert response.status_code == 404


def test_create_show_rejects_a_second_subscription_to_the_same_show(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    client.post("/api/shows", json={"tmdb_id": 95350})

    response = client.post("/api/shows", json={"tmdb_id": 95350})

    assert response.status_code == 409


def test_list_shows_returns_subscriptions(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    store.create_show(tmdb_id=1, title="A")
    store.create_show(tmdb_id=2, title="B")

    response = client.get("/api/shows")

    assert response.status_code == 200
    assert {s["tmdb_id"] for s in response.json()} == {1, 2}


def test_show_out_exposes_latest_request(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=1, title="A")

    no_history = client.get(f"/api/shows/{show.id}").json()
    assert no_history["latest_request"] is None

    store.create_episode_request(tmdb_id=1, show_id=show.id, title="A", season_number=1, episode_number=1)
    newest = store.create_episode_request(tmdb_id=1, show_id=show.id, title="A", season_number=1, episode_number=2)

    with_history = client.get(f"/api/shows/{show.id}").json()
    assert with_history["latest_request"]["id"] == newest.id
    assert with_history["latest_request"]["episode_number"] == 2


def test_list_shows_filters_by_status(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    watching = store.create_show(tmdb_id=1, title="A")
    paused = store.create_show(tmdb_id=2, title="B")
    store.update_show_status(paused.id, "paused")

    response = client.get("/api/shows", params={"status": "watching"})

    assert [s["id"] for s in response.json()] == [watching.id]


def test_get_show_404s_when_missing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/shows/999")
    assert response.status_code == 404


def test_pause_and_resume_show(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=1, title="A")

    paused = client.post(f"/api/shows/{show.id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/shows/{show.id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "watching"


def test_pause_show_404s_when_missing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.post("/api/shows/999/pause")
    assert response.status_code == 404


def test_unsubscribe_show_deletes_it_but_keeps_request_history(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=1, title="A")
    request_row = store.create_episode_request(
        tmdb_id=1, show_id=show.id, title="A", season_number=1, episode_number=1
    )

    response = client.delete(f"/api/shows/{show.id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert store.get_show(show.id) is None
    assert store.get_request(request_row.id) is not None


def test_unsubscribe_show_404s_when_missing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.delete("/api/shows/999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tv/{tmdb_id}/bulk-download — Stage 13/14.x bulk acquisition
# ---------------------------------------------------------------------------


def test_bulk_download_season_creates_a_pack_request_and_enqueues_it(client_and_deps):
    client, store, _, worker, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "season", "season_number": 1})

    assert response.status_code == 201
    body = response.json()
    assert body["media_type"] == "pack"
    assert body["show_id"] == show.id
    assert body["season_number"] == 1
    assert body["episode_number"] is None
    assert body["status"] == "queued"
    assert worker.enqueued == [body["id"]]


def test_bulk_download_accepts_redownload_mode_and_never_sets_a_floor(client_and_deps):
    """A request always means "the best copy within the household's
    Download settings": there is no per-request quality, only the
    redownload flag."""
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(
        f"/api/tv/{show.tmdb_id}/bulk-download",
        json={"scope": "season", "season_number": 1, "redownload_mode": "upgrade"},
    )

    assert response.status_code == 201
    body = response.json()
    assert store.get_request(body["id"]).min_resolution is None
    assert body["redownload_mode"] == "upgrade"


def test_following_a_show_a_season_add_left_paused_switches_it_on(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    anchor = client.post("/api/tv/95350/bulk-download", json={"scope": "season", "season_number": 1})
    assert anchor.status_code == 201 and store.get_show_by_tmdb_id(95350).status == "paused"

    followed = client.post("/api/shows", json={"tmdb_id": 95350})

    assert followed.status_code == 201, followed.text
    assert followed.json()["status"] == "watching"
    assert store.get_show_by_tmdb_id(95350).status == "watching"
    assert client.post("/api/shows", json={"tmdb_id": 95350}).status_code == 409


def test_bulk_download_of_a_whole_returning_show_also_follows_it(client_and_deps):
    client, store, _, _, _, _ = client_and_deps  # SHOW's status is "Returning Series"

    response = client.post("/api/tv/95350/bulk-download", json={"scope": "series", "season_number": None})

    assert response.status_code == 201, response.text
    assert store.get_show_by_tmdb_id(95350).status == "watching"


def test_bulk_download_of_one_season_or_an_ended_show_does_not_follow(client_and_deps):
    client, store, tmdb, _, _, _ = client_and_deps

    one_season = client.post("/api/tv/95350/bulk-download", json={"scope": "season", "season_number": 1})
    assert one_season.status_code == 201, one_season.text
    assert store.get_show_by_tmdb_id(95350).status == "paused"

    tmdb.get_tv = lambda tmdb_id: dict(SHOW, id=tmdb_id, status="Ended")
    ended = client.post("/api/tv/95350/bulk-download", json={"scope": "series", "season_number": None})
    assert ended.status_code == 201, ended.text
    assert store.get_show_by_tmdb_id(95350).status == "paused"


def test_bulk_download_rejects_unknown_redownload_mode(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(
        f"/api/tv/{show.tmdb_id}/bulk-download",
        json={"scope": "season", "season_number": 1, "redownload_mode": "delete-everything"},
    )

    assert response.status_code == 422


def test_bulk_download_series_creates_a_pack_request_with_no_season(client_and_deps):
    client, store, _, worker, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "series"})

    assert response.status_code == 201
    body = response.json()
    assert body["media_type"] == "pack"
    assert body["season_number"] is None
    assert worker.enqueued == [body["id"]]


def test_bulk_download_creates_a_paused_show_when_not_already_subscribed(client_and_deps):
    """The whole point of Stage 14.x's tmdb_id-keyed route: a bulk download
    must work before ever clicking "Add Show" — but the show it silently
    creates to anchor the request must be "paused", never "watching", so
    it doesn't also opt into the standing per-episode catch-up/recheck
    that was never asked for."""
    client, store, _, worker, _, _ = client_and_deps
    assert store.get_show_by_tmdb_id(95350) is None

    # A single season: a whole-series add on a returning show now also
    # follows it (see test_bulk_download_of_a_whole_returning_show_also_follows_it).
    response = client.post("/api/tv/95350/bulk-download", json={"scope": "season", "season_number": 1})

    assert response.status_code == 201
    show = store.get_show_by_tmdb_id(95350)
    assert show is not None
    assert show.status == "paused"
    assert show.title == "Lanterns"  # from FakeTMDBClient.get_tv via resolve_show
    assert worker.enqueued == [response.json()["id"]]


def test_bulk_download_reuses_an_existing_watching_show_unchanged(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "series"})

    assert response.status_code == 201
    assert response.json()["show_id"] == show.id
    assert store.get_show(show.id).status == "watching"  # untouched, not reset to paused


def test_bulk_download_on_a_new_show_persists_poster_path_from_the_fresh_resolve(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    assert store.get_show_by_tmdb_id(95350) is None

    response = client.post("/api/tv/95350/bulk-download", json={"scope": "series"})

    assert response.status_code == 201
    body = response.json()
    assert body["poster_path"] == "/lanterns.jpg"
    assert store.get_show_by_tmdb_id(95350).poster_path == "/lanterns.jpg"


def test_bulk_download_on_an_existing_show_reuses_its_stored_poster_path(client_and_deps):
    """The already-subscribed-show branch never re-resolves TMDB — the
    pack request's poster_path must come from the show row's own stored
    value, not a fresh (unmade) TMDB lookup."""
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns", poster_path="/already-on-file.jpg")

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "series"})

    assert response.status_code == 201
    assert response.json()["poster_path"] == "/already-on-file.jpg"


def test_bulk_download_404s_on_unknown_tmdb_id_when_no_show_exists(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_tv = True
    response = client.post("/api/tv/999999/bulk-download", json={"scope": "series"})
    assert response.status_code == 404


def test_bulk_download_series_cancels_every_still_queued_request_for_the_show(client_and_deps):
    """Subscribing (Stage 12) immediately queues catch-up requests for the
    latest season; asking for the complete series afterward made those
    redundant and, before this fix, left both running at once, flooding
    Requests with soon-superseded rows."""
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    ep1 = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=3, episode_number=1)
    ep2 = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=3, episode_number=2)
    already_downloading = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=3, episode_number=3
    )
    store.update_status(already_downloading.id, "downloading", result={"torrent_hash": "aaaa"})

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "series"})

    assert response.status_code == 201
    assert store.get_request(ep1.id).status == "cancelled"
    assert store.get_request(ep2.id).status == "cancelled"
    # Already in flight — a bulk action must never silently cancel real,
    # already-started work.
    assert store.get_request(already_downloading.id).status == "downloading"


def test_bulk_download_season_only_cancels_queued_requests_for_that_season(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    this_season = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=5, episode_number=1)
    other_season = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=2, episode_number=1)

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "season", "season_number": 5})

    assert response.status_code == 201
    assert store.get_request(this_season.id).status == "cancelled"
    assert store.get_request(other_season.id).status == "queued"  # unrelated season, left alone


def test_bulk_download_rejects_unknown_scope(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "everything"})

    assert response.status_code == 422


def test_bulk_download_rejects_season_scope_without_season_number(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "season"})

    assert response.status_code == 422


def test_bulk_download_rejects_series_scope_with_a_season_number(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    response = client.post(
        f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "series", "season_number": 1}
    )

    assert response.status_code == 422


def test_bulk_download_is_independent_of_watching_status(client_and_deps):
    """Usable regardless of paused/watching — bulk acquisition and the
    standing subscription are independent, not mutually exclusive."""
    client, store, _, worker, _, _ = client_and_deps
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    store.update_show_status(show.id, "paused")

    response = client.post(f"/api/tv/{show.tmdb_id}/bulk-download", json={"scope": "series"})

    assert response.status_code == 201
    assert worker.enqueued == [response.json()["id"]]


def test_list_requests_exposes_episode_fields(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    show = store.create_show(tmdb_id=1, title="A")
    store.create_episode_request(tmdb_id=1, show_id=show.id, title="A", season_number=1, episode_number=4)

    response = client.get("/api/requests")

    [row] = response.json()
    assert row["media_type"] == "episode"
    assert row["show_id"] == show.id
    assert row["season_number"] == 1
    assert row["episode_number"] == 4


def test_create_request_records_who_asked_for_it(client_and_deps):
    """frontend migration Part C2 — the fixture's own admin session."""
    client, store, _, _, _, _ = client_and_deps

    response = client.post("/api/requests", json={"tmdb_id": 693134, "query": "dune"})

    assert response.status_code == 201
    body = response.json()
    assert body["requested_by_plex_id"] == "admin-plex-id"
    assert body["requested_by_username"] == "admin"


# ---------------------------------------------------------------------------
# Auth: default-deny wiring, admin gating, setup bootstrap (frontend
# migration Part C/E/G1)
# ---------------------------------------------------------------------------


def test_unauthenticated_request_is_refused(unauthenticated_client):
    client, _, _, _, _, _ = unauthenticated_client

    response = client.get("/api/requests")

    assert response.status_code == 401


def test_a_route_with_no_explicit_auth_dependency_is_still_refused(unauthenticated_client):
    """Proves the default-deny *wiring*, not just that the allowlisted
    routes happen to work — a plain browse route declares no auth
    dependency of its own (it's carried entirely by `router`'s blanket
    one), so this only passes if that blanket dependency is actually
    attached, not merely present in the file."""
    client, _, _, _, _, _ = unauthenticated_client

    response = client.get("/api/discover/popular")

    assert response.status_code == 401


def test_non_admin_is_refused_admin_only_routes(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.get("/api/settings/pipeline")

    assert response.status_code == 403


def test_non_admin_can_still_use_regular_routes(non_admin_client):
    """require_session-only routes stay open to every signed-in user,
    not just the admin — the shared queue is deliberately shared."""
    client, _, _, _, _, _ = non_admin_client

    response = client.get("/api/requests")

    assert response.status_code == 200


def test_health_needs_no_auth_at_all(unauthenticated_client):
    client, _, _, _, _, _ = unauthenticated_client

    response = client.get("/api/health")

    assert response.status_code == 200


def test_setup_status_needs_no_auth(unauthenticated_client):
    client, _, _, _, _, _ = unauthenticated_client

    response = client.get("/api/setup/status")

    assert response.status_code == 200
    # The fixture's own store already has plex_token set (see
    # client_and_deps), so from setup's point of view this looks like an
    # already-completed install — matches every other test in this file.
    assert response.json()["setup_complete"] is True


def test_setup_mutation_410s_once_setup_is_already_complete(unauthenticated_client):
    client, _, _, _, _, _ = unauthenticated_client

    response = client.put("/api/setup/tmdb", json={"api_key": "new-key"})

    assert response.status_code == 410


def test_setup_mutation_refuses_missing_token_before_setup_completes(tmp_path, monkeypatch):
    """A fresh store (no plex_token at all) — the genuine bootstrap
    window, not the fixture's already-linked default. Also simulates a
    genuinely unconfigured environment: this repo's own backend/.env has
    a real TMDB_API_KEY, which would otherwise make setup_tmdb 409 here
    ("already configured via environment") instead of exercising the
    token gate this test is actually about."""
    monkeypatch.setattr(config, "TMDB_API_KEY", None)
    store = RequestStore(str(tmp_path / "fresh.db"))
    tmdb = FakeTMDBClient()
    worker = NoOpWorker()
    qbt = FakeQBTClient()

    @asynccontextmanager
    async def test_lifespan(app):
        app.state.store = store
        app.state.tmdb = tmdb
        app.state.worker = worker
        app.state.qbt = qbt
        app.state.plex_linker = FakePlexLinker()
        app.state.login_session = FakeLoginSession()
        yield

    api.app.router.lifespan_context = test_lifespan
    with TestClient(api.app) as client:
        response = client.put("/api/setup/tmdb", json={"api_key": "a-key"})
        assert response.status_code == 401

        token = store.get_or_create_setup_token()
        response = client.put(
            "/api/setup/tmdb", json={"api_key": "a-key"}, headers={"X-Setup-Token": "wrong-token"}
        )
        assert response.status_code == 401

        response = client.put(
            "/api/setup/tmdb", json={"api_key": "a-key"}, headers={"X-Setup-Token": token}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["tmdb_configured"] is True
        assert body["restart_required"] is True
        assert store.get_settings()["tmdb_api_key"] == "a-key"


def test_setup_tmdb_refuses_to_override_an_env_configured_key(unauthenticated_client, monkeypatch):
    client, store, _, _, _, _ = unauthenticated_client
    store.update_settings({"plex_server_machine_id": None})  # reopen the bootstrap window
    monkeypatch.setattr(config, "TMDB_API_KEY", "from-env")
    token = store.get_or_create_setup_token()

    response = client.put("/api/setup/tmdb", json={"api_key": "from-ui"}, headers={"X-Setup-Token": token})

    assert response.status_code == 409


def test_plex_link_bootstrap_requires_setup_token(unauthenticated_client):
    client, store, _, _, _, _ = unauthenticated_client
    store.update_settings({"plex_server_machine_id": None})  # reopen the bootstrap window

    response = client.post("/api/plex/link")
    assert response.status_code == 401

    token = store.get_or_create_setup_token()
    response = client.post("/api/plex/link", headers={"X-Setup-Token": token})
    assert response.status_code == 200


def test_plex_link_requires_admin_once_already_linked(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.post("/api/plex/link")

    assert response.status_code == 403


def test_login_start_returns_auth_url(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    response = client.post("/api/auth/login/start")

    assert response.status_code == 200
    assert "code=ABCD" in response.json()["auth_url"]


def test_login_status_sets_session_cookie_once_resolved(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    login_session = api_state_login_session(client)
    login_session._status = {
        "pending": False,
        "result": {"plex_user_id": "friend-1", "username": "friend", "is_admin": False},
        "error": None,
    }

    client.post("/api/auth/login/start")
    response = client.get("/api/auth/login/status")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["username"] == "friend"
    assert body["is_admin"] is False
    assert api.SESSION_COOKIE_NAME in response.cookies
    # And the new user is genuinely persisted, not just reflected back.
    assert store.get_user("friend-1").username == "friend"


def test_login_status_reports_pending_error_without_a_cookie(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    login_session = api_state_login_session(client)
    login_session._status = {"pending": False, "result": None, "error": "Your Plex account doesn't have access."}

    client.post("/api/auth/login/start")
    response = client.get("/api/auth/login/status")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert body["error"] == "Your Plex account doesn't have access."
    assert api.SESSION_COOKIE_NAME not in response.cookies


def test_get_current_session_reflects_the_signed_in_user(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json() == {"username": "regular-user", "is_admin": False, "has_seen_tutorial": False, "avatar": False}


def test_logout_clears_the_session_and_is_idempotent(client_and_deps):
    client, store, _, _, _, _ = client_and_deps

    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert client.get("/api/auth/session").status_code == 401

    # Calling it again with no session left at all must not error.
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def test_mark_tutorial_seen_persists(non_admin_client):
    client, store, _, _, _, _ = non_admin_client

    response = client.put("/api/auth/tutorial-seen")

    assert response.status_code == 200
    assert store.get_user("regular-plex-id").has_seen_tutorial is True


def test_list_plex_servers_returns_only_owned_ones(client_and_deps, monkeypatch):
    client, store, _, _, _, _ = client_and_deps
    monkeypatch.setattr(
        PlexClient,
        "list_resources",
        lambda self, token: [
            {"name": "Mine", "url": "http://a", "token": "t", "owned": True, "machine_identifier": "mid-a"},
            {"name": "Friend's", "url": "http://b", "token": "t", "owned": False, "machine_identifier": "mid-b"},
        ],
    )

    response = client.get("/api/plex/servers")

    assert response.status_code == 200
    assert response.json() == [{"name": "Mine", "machine_identifier": "mid-a"}]


def test_select_plex_server_switches_and_signs_out_other_sessions(client_and_deps, monkeypatch):
    client, store, _, _, _, _ = client_and_deps
    monkeypatch.setattr(
        PlexClient,
        "list_resources",
        lambda self, token: [
            {"name": "Second NAS", "url": "http://second", "token": "tok2", "owned": True, "machine_identifier": "mid-2"}
        ],
    )
    # A second, non-admin session that must not survive the switch.
    other = store.upsert_user("other-user", "other", False)
    store.create_session(
        "other-session", other.plex_user_id, other.username, False,
        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )

    response = client.put("/api/plex/server", json={"machine_identifier": "mid-2"})

    assert response.status_code == 200
    assert response.json() == {"server_name": "Second NAS"}
    assert store.get_settings()["plex_server_machine_id"] == "mid-2"
    assert store.get_session("other-session") is None


def test_select_plex_server_during_bootstrap_also_signs_the_admin_in(tmp_path, monkeypatch):
    """Completing setup's server picker is what finalizes bootstrap —
    without this, the admin would need a confusing second PIN sign-in
    immediately after the one that just linked the server."""
    store = RequestStore(str(tmp_path / "fresh.db"))
    store.update_settings({"plex_token": None, "plex_client_id": "client-1"})
    tmdb = FakeTMDBClient()
    worker = NoOpWorker()
    qbt = FakeQBTClient()

    @asynccontextmanager
    async def test_lifespan(app):
        app.state.store = store
        app.state.tmdb = tmdb
        app.state.worker = worker
        app.state.qbt = qbt
        app.state.plex_linker = FakePlexLinker()
        app.state.login_session = FakeLoginSession()
        yield

    api.app.router.lifespan_context = test_lifespan
    # pytest's monkeypatch fixture (not manual attribute assignment) —
    # it reverts automatically even on failure, unlike a hand-rolled
    # try/finally that's easy to leave incomplete (an earlier version of
    # this test restored list_resources but forgot get_account_identity,
    # which then leaked into every later test file in the same session —
    # e.g. test_plex.py's own get_account_identity tests, depending on
    # collection order).
    monkeypatch.setattr(
        PlexClient,
        "list_resources",
        lambda self, token: [
            {"name": "Home NAS", "url": "http://home", "token": "tok", "owned": True, "machine_identifier": "mid-x"}
        ],
    )
    monkeypatch.setattr(
        PlexClient, "get_account_identity", lambda self, token: {"id": 555, "username": "new-admin"}
    )
    with TestClient(api.app) as client:
        # Bootstrap window: plex_token isn't set yet at all (list_plex_servers/
        # select_plex_server read it from settings, so seed it directly —
        # in the real flow PlexLinker.start()+poll would have set it).
        store.update_settings({"plex_token": "linking-account-token"})
        token = store.get_or_create_setup_token()

        response = client.put(
            "/api/plex/server",
            json={"machine_identifier": "mid-x"},
            headers={"X-Setup-Token": token},
        )

        assert response.status_code == 200
        assert api.SESSION_COOKIE_NAME in response.cookies

        session_response = client.get("/api/auth/session")
        assert session_response.status_code == 200
        assert session_response.json() == {
            "username": "new-admin",
            "is_admin": True,
            "has_seen_tutorial": False,
            "avatar": False,
        }


# ---------------------------------------------------------------------------
# Frontend migration Part G4 — remote-access hardening: the Remote Access
# settings panel, the Secure-cookie flag it drives, "revoke all sessions",
# the audit log, and rate limiting on /api/auth/* + /api/setup/*.
# ---------------------------------------------------------------------------


def test_get_remote_access_defaults_to_disabled(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    response = client.get("/api/settings/remote-access")

    assert response.status_code == 200
    assert response.json() == {"remote_access_enabled": False, "public_domain": None}


def test_remote_access_requires_admin(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.get("/api/settings/remote-access")

    assert response.status_code == 403


def test_set_remote_access_persists_and_reads_back(client_and_deps):
    client, store, _, _, _, _ = client_and_deps

    response = client.put(
        "/api/settings/remote-access",
        json={"remote_access_enabled": True, "public_domain": "obsidian.example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"remote_access_enabled": True, "public_domain": "obsidian.example.com"}
    assert store.get_settings()["remote_access_enabled"] is True
    assert store.get_settings()["public_domain"] == "obsidian.example.com"


def test_set_remote_access_blanks_a_whitespace_only_domain_to_none(client_and_deps):
    client, store, _, _, _, _ = client_and_deps

    response = client.put(
        "/api/settings/remote-access", json={"remote_access_enabled": False, "public_domain": "   "}
    )

    assert response.status_code == 200
    assert response.json()["public_domain"] is None
    assert store.get_settings()["public_domain"] is None


def test_session_cookie_is_not_secure_by_default(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    login_session = api_state_login_session(client)
    login_session._status = {
        "pending": False,
        "result": {"plex_user_id": "cookie-user", "username": "cookie-user", "is_admin": False},
        "error": None,
    }

    client.post("/api/auth/login/start")
    response = client.get("/api/auth/login/status")

    set_cookie = response.headers.get("set-cookie", "")
    assert "session_id=" in set_cookie
    assert "Secure" not in set_cookie


def test_session_cookie_is_secure_once_remote_access_is_enabled(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    store.update_settings({"remote_access_enabled": True})
    login_session = api_state_login_session(client)
    login_session._status = {
        "pending": False,
        "result": {"plex_user_id": "cookie-user-2", "username": "cookie-user-2", "is_admin": False},
        "error": None,
    }

    # Over https, because that's what remote_access_enabled asserts is in
    # front of the app — and because the login attempt cookie is itself
    # Secure here, so a plain-http client would drop it before the status
    # poll and never reach the branch this test is about.
    client.post("https://testserver/api/auth/login/start")
    response = client.get("https://testserver/api/auth/login/status")

    assert "Secure" in response.headers.get("set-cookie", "")


def test_revoke_all_sessions_requires_admin(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.post("/api/admin/revoke-sessions")

    assert response.status_code == 403


def test_revoke_all_sessions_signs_out_every_session_including_the_caller(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    other = store.upsert_user("other-user", "other", False)
    store.create_session(
        "other-session", other.plex_user_id, other.username, False,
        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )

    response = client.post("/api/admin/revoke-sessions")

    assert response.status_code == 200
    assert response.json()["revoked"] >= 2
    assert store.get_session("other-session") is None
    # The admin's own session (the fixture's own cookie) is gone too.
    assert client.get("/api/auth/session").status_code == 401


def test_audit_log_requires_admin(non_admin_client):
    client, _, _, _, _, _ = non_admin_client

    response = client.get("/api/admin/audit-log")

    assert response.status_code == 403


def test_audit_log_records_login_success_and_failure(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    login_session = api_state_login_session(client)

    login_session._status = {
        "pending": False,
        "result": {"plex_user_id": "audit-user", "username": "audit-user", "is_admin": False},
        "error": None,
    }
    client.post("/api/auth/login/start")
    client.get("/api/auth/login/status")

    # A second attempt, because the first was spent claiming the session
    # above — a failure is only ever reported against a live attempt.
    login_session._status = {"pending": False, "result": None, "error": "no access"}
    client.post("/api/auth/login/start")
    client.get("/api/auth/login/status")

    # The login flow above replaced the client's cookie with the (non-admin)
    # audit-user's own session, via a real Set-Cookie response header — which
    # httpx's cookie jar stores against the TestClient's actual host, distinct
    # from the domain-less cookie the fixture set directly. Deleting by name
    # first (matches every domain) avoids ending up with both cookies present
    # at once, which is ambiguous about which one a request actually sends.
    client.cookies.delete(api.SESSION_COOKIE_NAME)
    client.cookies.set(api.SESSION_COOKIE_NAME, "test-admin-session")
    response = client.get("/api/admin/audit-log")

    assert response.status_code == 200
    body = response.json()
    event_types = [e["event_type"] for e in body["events"]]
    assert "login_success" in event_types
    assert "login_failure" in event_types
    failure_event = next(e for e in body["events"] if e["event_type"] == "login_failure")
    assert failure_event["detail"] == "no access"
    success_event = next(e for e in body["events"] if e["event_type"] == "login_success")
    assert success_event["username"] == "audit-user"


def test_audit_log_records_remote_access_toggle(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    client.put("/api/settings/remote-access", json={"remote_access_enabled": True, "public_domain": None})

    events = client.get("/api/admin/audit-log").json()["events"]
    assert any(e["event_type"] == "remote_access_toggled" and "enabled" in e["detail"] for e in events)


def test_audit_log_records_connections_changes(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(config, "TMDB_API_KEY", None)
    monkeypatch.setattr(api.config, "_QBIT_HOST_ENV", None)

    client.put("/api/settings/tmdb", json={"api_key": "a-new-key"})
    client.put(
        "/api/settings/qbittorrent", json={"host": "10.0.0.9", "port": 1234, "username": "", "password": ""}
    )

    events = [e["event_type"] for e in client.get("/api/admin/audit-log").json()["events"]]
    assert "tmdb_key_changed" in events
    assert "qbt_connection_changed" in events


def test_audit_log_records_deploy_trigger(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api, "run_git_pull", lambda: {"detail": "ok", "commit": "abc1234"})

    client.post("/api/admin/deploy")

    events = client.get("/api/admin/audit-log").json()["events"]
    assert any(e["event_type"] == "deploy_triggered" and "abc1234" in e["detail"] for e in events)


def test_audit_log_records_deploy_failure(client_and_deps, monkeypatch):
    client, _, _, _, _, _ = client_and_deps

    def _boom():
        raise api.DeployError("git pull failed: conflict")

    monkeypatch.setattr(api, "run_git_pull", _boom)

    response = client.post("/api/admin/deploy")

    assert response.status_code == 502
    events = client.get("/api/admin/audit-log").json()["events"]
    assert any(e["event_type"] == "deploy_failed" for e in events)


def test_audit_log_records_plex_unlink(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    client.post("/api/plex/unlink")

    events = [e["event_type"] for e in client.get("/api/admin/audit-log").json()["events"]]
    assert "plex_unlinked" in events


def test_audit_log_paginates(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    for i in range(5):
        store.record_auth_event("test_event", detail=f"event {i}")

    response = client.get("/api/admin/audit-log", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 2
    assert body["total"] >= 5


def test_login_start_is_rate_limited(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    responses = [client.post("/api/auth/login/start") for _ in range(21)]

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) <= 20
    assert 429 in statuses


def test_login_status_limit_outpaces_the_frontend_poll():
    """The login page polls this endpoint on a fixed interval for as long
    as the PIN is unresolved, so its limit has to sit above that rate
    with room to spare — at 20/minute against a 2.5s poll it 429'd itself
    ~50s in and the login silently died (the poll then stopped for good).
    Asserted as arithmetic against the poll interval rather than a bare
    number, so changing POLL_INTERVAL_MS in useEndUserLogin.ts without
    revisiting this lights up here instead of in someone's first
    sign-in."""
    frontend_poll_interval_seconds = 2.5
    polls_per_minute = 60 / frontend_poll_interval_seconds

    allowed, _, per_minute = api.STATUS_POLL_RATE_LIMIT.partition("/")

    assert per_minute == "minute"
    # 2x, not 1x: two open login tabs, plus the retries that ride on top
    # of the poll, all land in the same per-IP bucket.
    assert int(allowed) >= polls_per_minute * 2


def _finished_login(client, plex_user_id: str) -> "FakeLoginSession":
    """A login session whose PIN has resolved to `plex_user_id` — the
    state every one of the claim tests below starts from."""
    login_session = api_state_login_session(client)
    login_session._status = {
        "pending": False,
        "result": {"plex_user_id": plex_user_id, "username": plex_user_id, "is_admin": False},
        "error": None,
    }
    return login_session


def test_login_start_issues_an_attempt_cookie(client_and_deps):
    client, _, _, _, _, _ = client_and_deps

    response = client.post("/api/auth/login/start")

    assert response.status_code == 200
    assert api.LOGIN_ATTEMPT_COOKIE_NAME in response.cookies


def test_login_status_does_not_hand_a_session_to_a_bystander(client_and_deps):
    """The endpoint is unauthenticated by design, so "is a login
    finished?" must be answerable only by the browser that *started*
    that login. Otherwise any unauthenticated caller who happens to poll
    while someone else's PIN is resolved walks away with a session
    cookie for that person's account."""
    client, _, _, _, _, _ = client_and_deps
    _finished_login(client, "victim")

    client.post("/api/auth/login/start")
    claimed = client.get("/api/auth/login/status")
    assert claimed.json()["authenticated"] is True

    # A different browser: never called /start, carries no cookies.
    client.cookies.clear()
    bystander = client.get("/api/auth/login/status")

    assert bystander.json()["authenticated"] is False
    assert api.SESSION_COOKIE_NAME not in bystander.cookies


def test_login_status_with_an_unknown_attempt_id_mints_nothing(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    _finished_login(client, "victim")
    client.post("/api/auth/login/start")

    client.cookies.clear()
    client.cookies.set(api.LOGIN_ATTEMPT_COOKIE_NAME, "not-a-real-attempt-id")
    guessed = client.get("/api/auth/login/status")

    assert guessed.json()["authenticated"] is False
    assert api.SESSION_COOKIE_NAME not in guessed.cookies


def test_a_completed_login_is_claimable_only_once(client_and_deps):
    """One finished sign-in is one session. Left claimable, a single
    resolved PIN keeps minting fresh sessions on every later poll."""
    client, _, _, _, _, _ = client_and_deps
    _finished_login(client, "claim-once")

    client.post("/api/auth/login/start")
    first = client.get("/api/auth/login/status")
    assert first.json()["authenticated"] is True

    second = client.get("/api/auth/login/status")

    assert second.json()["authenticated"] is False
    assert api.SESSION_COOKIE_NAME not in second.cookies


def api_state_login_session(client) -> "FakeLoginSession":
    """The FakeLoginSession instance the running app's lifespan installed
    — reached through the TestClient's own app reference, since the
    fixture doesn't hand it back directly (login flow tests are the only
    ones that need to reach into it)."""
    return client.app.state.login_session


# -- Obsidian feature pass: quality profiles, per-episode status, storage
#    details, Plex on-deck --


def test_request_episode_and_season_status(client_and_deps):
    client, store, tmdb, worker, _, _ = client_and_deps
    tmdb.get_tv_season = lambda tmdb_id, season_number: [
        {"episode_number": 1, "name": "Pilot", "air_date": "2020-01-01", "runtime": 50, "still_path": "/e1.jpg"},
        {"episode_number": 2, "name": "Two", "air_date": "2020-01-08", "runtime": 48, "still_path": None},
        {"episode_number": 3, "name": "Later", "air_date": "2999-01-01", "runtime": None, "still_path": None},
    ]
    created = client.post("/api/tv/95350/episodes/1/1")
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["media_type"] == "episode"
    assert request_id in worker.enqueued
    assert store.get_show_by_tmdb_id(95350).status == "paused"

    again = client.post("/api/tv/95350/episodes/1/1")
    assert again.status_code == 409

    body = client.get("/api/tv/95350/season/1/episodes").json()
    by_number = {e["episode_number"]: e for e in body["episodes"]}
    assert by_number[1]["state"] == "requested" and by_number[1]["status"] == "queued"
    assert by_number[1]["request_id"] == request_id
    assert by_number[2]["state"] == "missing"
    assert by_number[3]["state"] == "unaired"
    assert body["aired"] == 2 and body["in_plex"] == 0

    store.update_status(request_id, "complete")
    body = client.get("/api/tv/95350/season/1/episodes").json()
    assert {e["episode_number"]: e["state"] for e in body["episodes"]}[1] == "in_plex"
    assert body["in_plex"] == 1


def test_storage_details_shape(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    body = client.get("/api/storage/details").json()
    assert [lib["key"] for lib in body["libraries"]] == ["movies", "tv"]
    for key in ("downloading", "queued", "completed_today", "completed_week"):
        assert isinstance(body[key], int)


def test_plex_on_deck_and_image_unlinked(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    assert client.get("/api/plex/on-deck").json() == {"available": False, "items": []}
    assert client.get("/api/plex/image", params={"path": "/library/metadata/1/art/2"}).status_code == 404
    assert client.get("/api/plex/image", params={"path": "/etc/passwd"}).status_code == 400


def test_discover_browse_passes_every_filter_through(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover", params={"genre": 28, "provider": 8, "year": 2024, "sort": "rated", "page": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [dict(MOVIE, on_plex=False)]
    assert body["filters"] == {"genre_id": 28, "provider_id": 8, "year": 2024, "sort": "rated", "region": "US", "page": 3}


def test_discover_browse_rejects_unknown_sort(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/discover", params={"sort": "bogus"})

    assert response.status_code == 400


def test_tv_discover_browse_is_not_shadowed_by_the_detail_route(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/tv/discover", params={"genre": 18, "sort": "newest"})

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [dict(SHOW, on_plex=False)]
    assert body["filters"]["genre_id"] == 18
    assert body["filters"]["sort"] == "newest"


def test_plex_recently_added_reports_unavailable_when_unlinked(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    response = client.get("/api/plex/recently-added")

    assert response.status_code == 200
    assert response.json() == {"available": False, "items": []}


def test_household_lists_users_and_admin_cannot_be_switched_off(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    users = client.get("/api/admin/users").json()
    assert users and users[0]["is_admin"] is True
    admin_id = users[0]["plex_user_id"]
    assert client.put(f"/api/admin/users/{admin_id}", json={"can_request": False}).status_code == 400
    assert client.delete(f"/api/admin/users/{admin_id}").status_code == 400
    assert client.put("/api/admin/users/nobody", json={"can_request": False}).status_code == 404


def test_library_settings_round_trip_and_floor(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    current = client.get("/api/settings/library").json()
    assert "movie_library_root" in current and current["plex_refresh_after_import"] is True
    response = client.put(
        "/api/settings/library",
        json={"plex_refresh_after_import": False, "free_space_floor_gb": 200},
    )
    assert response.status_code == 200
    assert response.json()["plex_refresh_after_import"] is False
    assert response.json()["free_space_floor_gb"] == 200
    assert client.put("/api/settings/library", json={"free_space_floor_gb": -1}).status_code == 422


def test_about_reports_the_basics(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    body = client.get("/api/about").json()
    assert body["name"] == "Obsidian"
    assert "requests" in body and "users" in body


def test_best_logo_prefers_english_png_with_votes():
    from app.tmdb import best_logo_path

    assert best_logo_path(None) is None
    assert best_logo_path({"logos": []}) is None
    logos = [
        {"iso_639_1": None, "file_path": "/plain.png", "vote_count": 9},
        {"iso_639_1": "en", "file_path": "/en.svg", "vote_count": 20},
        {"iso_639_1": "en", "file_path": "/en-low.png", "vote_count": 1},
        {"iso_639_1": "en", "file_path": "/en-top.png", "vote_count": 5},
    ]
    assert best_logo_path({"logos": logos}) == "/en-top.png"


def test_movie_and_tv_detail_carry_a_logo_path(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    assert "logo_path" in client.get("/api/movies/1").json()
    assert "logo_path" in client.get("/api/tv/1").json()


def test_session_reports_avatar_and_proxy_404s_without_one(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    assert client.get("/api/auth/session").json()["avatar"] is False
    assert client.get("/api/me/avatar").status_code == 404


def test_upsert_user_keeps_an_avatar_when_login_brings_none(tmp_path):
    from app.db import RequestStore

    store = RequestStore(tmp_path / "a.db")
    store.upsert_user("u1", "Uno", False, "https://plex.tv/users/abc/avatar")
    again = store.upsert_user("u1", "Uno", False, None)
    assert again.avatar_url == "https://plex.tv/users/abc/avatar"


def test_plex_locate_degrades_when_unlinked_and_rejects_bad_type(client_and_deps):
    client, _, _, _, _, _ = client_and_deps
    assert client.get("/api/plex/locate", params={"type": "movie", "title": "Undertow"}).json() == {"available": False}
    assert client.get("/api/plex/locate", params={"type": "song", "title": "x"}).status_code == 400


def test_reject_current_copy_uses_the_library_ledger_when_this_app_filed_it(client_and_deps, tmp_path):
    """Obsidian's own copy, request history long cleared: the ledger still
    knows the torrent hash and release, so both are blacklisted and the
    file removed."""
    client, store, _, _, _, _ = client_and_deps
    filed = tmp_path / "Mutiny (2026).mkv"
    filed.write_bytes(b"x" * 20)
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "dead", "winner": {"fileName": "Mutiny.2026.2160p.REMUX.mkv"}})
    store.mark_organized(row.id, [str(filed)], ["dead"], "2000-01-01T00:00:00+00:00")
    store.purge_requests_older_than(days=0)
    assert client.get("/api/movies/693134").json()["on_plex_tracked"] is True

    response = client.post("/api/movies/693134/reject-current")

    assert response.status_code == 200, response.text
    assert response.json() == {"removed": [filed.name]}
    assert not filed.exists()
    assert store.get_rejected_torrent_hashes(693134) == {"dead"}
    assert store.get_rejected_releases(693134) == [{"name": "Mutiny.2026.2160p.REMUX.mkv", "size_bytes": None}]
    assert store.get_library_items(693134) == []


def test_reject_current_copy_for_a_file_this_app_never_added(client_and_deps, monkeypatch, tmp_path):
    """The End of Oak Street (live 2026-09-17): on Plex, but not added by
    Obsidian, so "This copy is broken" was greyed out. Plex points at the
    file; it's deleted and its release name blacklisted."""
    from app import api

    filed = tmp_path / "The.End.of.Oak.Street.2025.2160p.WEB-DL.mkv"
    filed.write_bytes(b"broken")
    monkeypatch.setattr(api, "local_file_for_title", lambda store, media_type, title, year, tmdb_id=None: filed)
    client, store, _, _, _, _ = client_and_deps

    response = client.post("/api/movies/693134/reject-current")

    assert response.status_code == 200, response.text
    assert response.json() == {"removed": [filed.name]}
    assert not filed.exists()
    assert store.get_rejected_releases(693134) == [{"name": "The.End.of.Oak.Street.2025.2160p.WEB-DL", "size_bytes": 6}]

    monkeypatch.setattr(api, "local_file_for_title", lambda store, media_type, title, year, tmdb_id=None: None)
    assert client.post("/api/movies/693134/reject-current").status_code == 409


def test_overwrite_is_allowed_when_plex_can_point_at_the_file(client_and_deps, monkeypatch, tmp_path):
    from app import api

    client, _, _, _, _, _ = client_and_deps
    monkeypatch.setattr(api, "local_file_for_title", lambda store, media_type, title, year, tmdb_id=None: None)
    assert client.post("/api/requests", json={"tmdb_id": 693134, "redownload_mode": "overwrite"}).status_code == 400

    filed = tmp_path / "Dune.mkv"
    filed.write_bytes(b"x")
    monkeypatch.setattr(api, "local_file_for_title", lambda store, media_type, title, year, tmdb_id=None: filed)
    assert client.post("/api/requests", json={"tmdb_id": 693134, "redownload_mode": "overwrite"}).status_code == 201
    detail = client.get("/api/movies/693134").json()
    assert detail["plex_file_available"] is (detail["on_plex"] is True)
