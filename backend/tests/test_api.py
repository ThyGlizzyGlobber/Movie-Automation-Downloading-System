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

SHOW = {
    "id": 95350,
    "name": "Lanterns",
    "original_name": "Lanterns",
    "first_air_date": "2026-01-01",
    "status": "Returning Series",
    "number_of_seasons": 1,
}


class FakeTMDBClient:
    def __init__(
        self, search_results=None, movie=None, raise_on_get_movie=False, tv_search_results=None, raise_on_get_tv=False
    ):
        self._search_results = search_results if search_results is not None else [MOVIE]
        self._movie = movie or MOVIE
        self._raise_on_get_movie = raise_on_get_movie
        self._tv_search_results = tv_search_results if tv_search_results is not None else [SHOW]
        self._raise_on_get_tv = raise_on_get_tv

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

    def discover_by_provider(self, provider_id, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 10, "provider_id": provider_id}

    def discover_by_genre(self, genre_id, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 10, "genre_id": genre_id}

    def get_coming_soon(self, region="US", page=1):
        return {"results": [MOVIE], "page": page, "total_pages": 3}

    # -- Stage 12: show subscriptions --

    def get_tv(self, tmdb_id):
        if self._raise_on_get_movie or self._raise_on_get_tv:
            from app.tmdb import TMDBError

            raise TMDBError("not found")
        return dict(SHOW, id=tmdb_id)

    # -- Stage 14: TV browse surface --

    def get_tv_popular(self, page=1):
        return {"results": [SHOW], "page": page, "total_pages": 500}

    def get_tv_trending(self, time_window="week", page=1):
        return {"results": [SHOW], "page": page}

    def search_tv(self, query, year=None):
        return {"results": self._tv_search_results}

    def search_tv_within_provider(self, query, provider_id, region="US"):
        return {"results": self._tv_search_results, "provider_id": provider_id}

    def discover_tv_by_provider(self, provider_id, region="US", page=1):
        return {"results": [SHOW], "page": page, "total_pages": 10, "provider_id": provider_id}

    def discover_tv_by_genre(self, genre_id, region="US", page=1):
        return {"results": [SHOW], "page": page, "total_pages": 10, "genre_id": genre_id}

    def get_tv_season(self, tmdb_id, season_number):
        return []


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
    "reset to default", same convention as AppearanceSettings/
    RetentionSettings), but the endpoint itself doesn't assume that — an
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


def test_set_tv_settings_persists_and_reads_back(client_and_deps):
    client, store, _, _, _, _ = client_and_deps
    response = client.put(
        "/api/settings/tv",
        json={
            "show_check_interval_hours": 2,
            "episode_recheck_enabled": True,
            "episode_recheck_interval_hours": 0.5,
            "episode_recheck_max_attempts": 0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "show_check_interval_hours": 2,
        "episode_recheck_enabled": True,
        "episode_recheck_interval_hours": 0.5,
        "episode_recheck_max_attempts": 0,
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
    assert response.json() == dict(MOVIE, on_plex=False)


def test_get_movie_detail_404s_on_unknown_tmdb_id(client_and_deps):
    client, _, tmdb, _, _, _ = client_and_deps
    tmdb._raise_on_get_movie = True

    response = client.get("/api/movies/999999")

    assert response.status_code == 404


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
    assert response.json() == dict(SHOW, on_plex=False)


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

    response = client.post("/api/tv/95350/bulk-download", json={"scope": "series"})

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
