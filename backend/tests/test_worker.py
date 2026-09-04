import asyncio
import re

import pytest

from app import config
from app.db import RequestStore
from app.qbt import QBTError
from app.worker import Worker


@pytest.fixture(autouse=True)
def _fast_hash_capture(monkeypatch):
    """See test_pipeline.py's fixture of the same name — avoids real
    sleeps in pipeline.download()'s torrent-hash capture retry loop."""
    monkeypatch.setattr(config, "HASH_CAPTURE_ATTEMPTS", 1)
    monkeypatch.setattr(config, "HASH_CAPTURE_INTERVAL_SECONDS", 0)

MOVIE = {
    "title": "Dune: Part Two",
    "original_title": "Dune: Part Two",
    "release_date": "2024-03-01",
}

_BTIH_RE = re.compile(r"btih:([a-zA-Z0-9]+)")


class FakeTMDBClient:
    def get_movie(self, tmdb_id):
        return MOVIE


class FakeQBTClient:
    """Same shape as test_pipeline's fake, plus torrent_info() for the
    download watcher."""

    def __init__(self, results_by_variant=None, existing_hashes=None, free_space_bytes=1_000_000_000_000, torrent_states=None):
        self.results_by_variant = results_by_variant or {}
        self._existing_hashes = existing_hashes or set()
        self._free_space_bytes = free_space_bytes
        self._torrent_states = torrent_states or {}
        self.added: list[tuple[str, str]] = []

    def search(self, pattern, category="movies", plugins="enabled"):
        return self.results_by_variant.get(pattern, [])

    def existing_torrent_hashes(self):
        return set(self._existing_hashes)

    def free_space_bytes(self):
        return self._free_space_bytes

    def ensure_category(self, category):
        pass

    def add_torrent(self, file_url, category):
        self.added.append((file_url, category))
        match = _BTIH_RE.search(file_url)
        if match:
            self._existing_hashes.add(match.group(1).lower())

    def torrent_info(self, torrent_hash):
        return self._torrent_states.get(torrent_hash)


def _result(**overrides):
    base = {
        "engineName": "piratebay",
        "fileName": "Dune.Part.Two.2024.2160p.REMUX.mkv",
        "fileUrl": "magnet:?xt=urn:btih:AAAA",
        "fileSize": 40_000_000_000,
        "nbSeeders": 100,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _run_one — one pipeline pass for a single queued row
# ---------------------------------------------------------------------------


def test_run_one_marks_downloading_and_captures_hash():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    qbt = FakeQBTClient(results_by_variant={"Dune: Part Two": [_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloading"
    assert reloaded.result["torrent_hash"] == "aaaa"
    assert reloaded.result["winner"]["fileName"] == "Dune.Part.Two.2024.2160p.REMUX.mkv"
    assert reloaded.result["score"]["composite"] > 0


def test_run_one_marks_no_qualifying_results():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient(results_by_variant={}))

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "no qualifying results"


def test_run_one_marks_insufficient_free_space():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileSize=90_000_000_000)]},
        free_space_bytes=10_000_000_000,
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "insufficient free space"


def test_run_one_marks_failed_with_audit_trail_when_every_candidate_fails_to_add():
    """The real bug this closes (a limetorrents link qBittorrent accepted
    but never actually fetched): request status is "failed" like any other
    failure, but — unlike a bare exception — `result` still carries which
    candidate was tried, so the request stays auditable."""
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)

    class RejectingQBTClient(FakeQBTClient):
        def add_torrent(self, file_url, category):
            raise QBTError("qBittorrent rejected the add ('Fails.')")

    qbt = RejectingQBTClient(results_by_variant={"Dune: Part Two": [_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    reloaded = store.get_request(row.id)
    assert reloaded.status == "failed"
    assert "qBittorrent couldn't add any candidate release" in reloaded.error_message
    assert reloaded.result["winner"]["fileName"] == "Dune.Part.Two.2024.2160p.REMUX.mkv"
    assert "rejected the add" in reloaded.result["add_error"]


def test_run_one_marks_failed_on_exception_and_keeps_message():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)

    class BoomTMDBClient:
        def get_movie(self, tmdb_id):
            raise RuntimeError("tmdb is down")

    worker = Worker(store, BoomTMDBClient(), FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    reloaded = store.get_request(row.id)
    assert reloaded.status == "failed"
    assert "tmdb is down" in reloaded.error_message


def test_run_one_skips_row_not_in_queued_state():
    """A stale queue entry (e.g. re-enqueued across a restart for a row
    that already finished) must not be reprocessed."""
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "complete")
    qbt = FakeQBTClient(results_by_variant={"Dune: Part Two": [_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "complete"
    assert qbt.added == []


# ---------------------------------------------------------------------------
# _check_downloading — the download-progress watcher
# ---------------------------------------------------------------------------


def test_check_downloading_marks_complete_when_progress_reaches_one():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "complete"


def test_check_downloading_leaves_in_progress_torrent_alone():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 0.4}})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "downloading"


def test_check_downloading_marks_cancelled_when_torrent_is_gone():
    """A torrent deleted directly in qBittorrent (not through this app)
    must not leave the request stuck at "downloading" forever."""
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={})  # "aaaa" absent -> torrent_info returns None
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "cancelled"
    assert reloaded.error_message == "Removed from qBittorrent outside this app"


def test_check_downloading_marks_complete_when_gone_but_plex_has_it(monkeypatch):
    """qBittorrent's own "remove torrent after completion" setting makes a
    *finished* download disappear the same way a deleted one would — Plex
    is the tie-breaker before assuming the worse case."""
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={})
    monkeypatch.setattr("app.worker.plex.has_in_library", lambda store, title, year: True)
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "complete"


def test_check_downloading_marks_cancelled_with_plex_checked_message_when_not_found(monkeypatch):
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={})
    monkeypatch.setattr("app.worker.plex.has_in_library", lambda store, title, year: False)
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "cancelled"
    assert reloaded.error_message == "Removed from qBittorrent outside this app, and not found in Plex"


def test_check_downloading_skips_rows_without_a_captured_hash():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": None})
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "downloading"


# ---------------------------------------------------------------------------
# _cleanup_old_requests — automatic retention purge
# ---------------------------------------------------------------------------


def test_cleanup_old_requests_noop_when_no_retention_policy_set():
    store = RequestStore(":memory:")
    old = store.create_request(tmdb_id=1, title="Old", release_year=2020, query=None)
    store.update_status(old.id, "complete")
    store._conn.execute(
        "UPDATE requests SET created_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", old.id),
    )
    store._conn.commit()
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._cleanup_old_requests())

    assert store.get_request(old.id) is not None


def test_cleanup_old_requests_purges_per_configured_retention():
    store = RequestStore(":memory:")
    store.update_settings({"request_retention_days": 90})
    old = store.create_request(tmdb_id=1, title="Old", release_year=2020, query=None)
    store.update_status(old.id, "complete")
    store._conn.execute(
        "UPDATE requests SET created_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", old.id),
    )
    store._conn.commit()
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._cleanup_old_requests())

    assert store.get_request(old.id) is None


# ---------------------------------------------------------------------------
# start() — boot recovery + requeueing rows left "queued" from before a
# restart
# ---------------------------------------------------------------------------


def test_start_recovers_interrupted_searching_row_and_drains_queued_row():
    store = RequestStore(":memory:")
    stuck = store.create_request(tmdb_id=1, title="A", release_year=2020, query=None)
    store.update_status(stuck.id, "searching")
    queued = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    qbt = FakeQBTClient(results_by_variant={"Dune: Part Two": [_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    async def run():
        await worker.start()
        for _ in range(50):
            if store.get_request(queued.id).status not in ("queued", "searching"):
                break
            await asyncio.sleep(0.05)
        await worker.stop()

    asyncio.run(run())

    assert store.get_request(stuck.id).status == "failed"
    assert store.get_request(stuck.id).error_message == "interrupted, please retry"
    assert store.get_request(queued.id).status == "downloading"
