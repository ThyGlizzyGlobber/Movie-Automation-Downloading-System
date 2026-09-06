import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.db import RequestStore, ShowEpisodeRow
from app.qbt import QBTError
from app.tv_settings import TVScheduleSettings
from app.worker import Worker


def _backdate_show_episode(store: RequestStore, show_id: int, season: int, episode: int, hours_ago: float) -> None:
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    store._conn.execute(
        "UPDATE show_episodes SET created_at = ? WHERE show_id = ? AND season_number = ? AND episode_number = ?",
        (when, show_id, season, episode),
    )
    store._conn.commit()


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

SHOW = {
    "id": 95350,
    "name": "Lanterns",
    "original_name": "Lanterns",
    "first_air_date": "2026-08-16",
    "number_of_seasons": 1,
}

_BTIH_RE = re.compile(r"btih:([a-zA-Z0-9]+)")


class FakeTMDBClient:
    def __init__(self, show=None, season_episodes=None):
        self._show = show or SHOW
        self._season_episodes = season_episodes if season_episodes is not None else {}

    def get_movie(self, tmdb_id):
        return MOVIE

    def get_tv(self, tmdb_id):
        return self._show

    def get_tv_season(self, tmdb_id, season_number):
        return self._season_episodes.get(season_number, [])


class FakeQBTClient:
    """Same shape as test_pipeline's fake, plus torrent_info() for the
    download watcher and torrent_files() for the file organizer."""

    def __init__(
        self,
        results_by_variant=None,
        existing_hashes=None,
        free_space_bytes=1_000_000_000_000,
        torrent_states=None,
        torrent_files=None,
    ):
        self.results_by_variant = results_by_variant or {}
        self._existing_hashes = existing_hashes or set()
        self._free_space_bytes = free_space_bytes
        self._torrent_states = torrent_states or {}
        self._torrent_files = torrent_files or {}
        self.added: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, bool]] = []

    def ping(self):
        return True

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

    def torrent_files(self, torrent_hash):
        return self._torrent_files.get(torrent_hash, [])

    def delete_torrent(self, torrent_hash, delete_files=True):
        self.deleted.append((torrent_hash, delete_files))
        self._torrent_states.pop(torrent_hash, None)


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


def test_run_one_logs_pipeline_result_with_variant_and_score(caplog):
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    qbt = FakeQBTClient(results_by_variant={"Dune: Part Two": [_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    with caplog.at_level("INFO", logger="app.worker"):
        asyncio.run(worker._run_one(row.id))

    [record] = [r for r in caplog.records if "pipeline result" in r.message]
    assert "variant='Dune: Part Two'" in record.message
    assert "score=composite:" in record.message


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


def test_check_downloading_marks_complete_when_progress_reaches_one(caplog):
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)

    with caplog.at_level("INFO", logger="app.worker"):
        asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "complete"
    assert any("downloading -> complete" in r.message for r in caplog.records)


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


# ---------------------------------------------------------------------------
# _run_one — episode requests (Stage 12)
# ---------------------------------------------------------------------------


def _episode_result(**overrides):
    base = {
        "engineName": "piratebay",
        "fileName": "Lanterns.S01E01.2160p.WEB-DL.mkv",
        "fileUrl": "magnet:?xt=urn:btih:BBBB",
        "fileSize": 8_000_000_000,
        "nbSeeders": 100,
    }
    base.update(overrides)
    return base


def test_run_one_downloads_an_episode_request():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    qbt = FakeQBTClient(results_by_variant={"Lanterns S01E01": [_episode_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloading"
    assert reloaded.result["torrent_hash"] == "bbbb"


def test_run_one_marks_episode_no_qualifying_results():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=4
    )
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "no qualifying results"


# ---------------------------------------------------------------------------
# _check_downloading — episode organize-on-complete gate (Stage 12)
# ---------------------------------------------------------------------------


def test_check_downloading_organizes_episode_and_marks_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "downloads" / "Lanterns.S01E01.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"data")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": str(source.parent)}},
        torrent_files={"aaaa": [{"name": source.name, "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "complete"
    target = tmp_path / "library" / "Lanterns (2026) {tmdb-95350}" / "Season 01" / "Lanterns - s01e01.mkv"
    assert target.exists()


def test_check_downloading_marks_episode_downloaded_not_filed_when_organize_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})  # no save_path -> MediaOrganizerError
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloaded, not filed"
    assert reloaded.error_message is not None


def test_check_downloading_marks_episode_cancelled_when_gone_without_asking_plex(monkeypatch):
    """Unlike a movie row, an episode disappearance never consults Plex —
    `PlexClient.has_movie` only supports a movie-shaped lookup."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={})

    def _boom(*args, **kwargs):
        raise AssertionError("plex.has_in_library should not be called for episode rows")

    monkeypatch.setattr("app.worker.plex.has_in_library", _boom)
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "cancelled"
    assert reloaded.error_message == "Removed from qBittorrent outside this app"


# ---------------------------------------------------------------------------
# check_show / _check_all_watching_shows — Stage 12 subscription scheduler
# ---------------------------------------------------------------------------


def test_check_show_creates_and_enqueues_requests_for_aired_unhandled_episodes():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        season_episodes={
            1: [
                {"episode_number": 1, "air_date": "2020-01-01"},
                {"episode_number": 2, "air_date": "2020-01-08"},
                {"episode_number": 3, "air_date": "2099-01-01"},  # unaired
            ]
        }
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show)

    assert created == 2
    requests = store.list_requests()
    assert {(r.season_number, r.episode_number) for r in requests} == {(1, 1), (1, 2)}
    assert all(r.media_type == "episode" and r.status == "queued" for r in requests)
    assert store.has_show_episode(show.id, 1, 1)
    assert store.has_show_episode(show.id, 1, 2)
    assert not store.has_show_episode(show.id, 1, 3)
    assert store.get_show(show.id).last_checked_at is not None
    assert worker.queue.qsize() == 2


def test_check_show_marks_episode_complete_without_downloading_when_already_on_disk(tmp_path, monkeypatch):
    """A first-time subscribe to a show that already has episodes on disk
    (e.g. an earlier manual/CLI-only download that never touched the
    ledger) must not re-search-and-re-add them."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    (tmp_path / "Lanterns.S01E01.2160p.WEB-DL.mkv").write_bytes(b"data")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}]})
    qbt = FakeQBTClient()
    worker = Worker(store, tmdb, qbt)

    created = worker.check_show(show)

    assert created == 0  # nothing was actually enqueued for download
    assert qbt.added == []
    [request_row] = store.list_requests()
    assert request_row.status == "complete"
    assert request_row.media_type == "episode"
    assert "already on disk" in request_row.result["note"]
    assert store.has_show_episode(show.id, 1, 1)


def test_check_show_skips_episodes_already_in_ledger():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    store.add_show_episode(show.id, 1, 1, request_id=1)
    tmdb = FakeTMDBClient(season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}]})
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show)

    assert created == 0
    assert store.list_requests() == []


def test_check_show_returns_zero_and_leaves_last_checked_unset_on_tmdb_error(caplog):
    from app.tmdb import TMDBError

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    class BoomTMDBClient:
        def get_tv(self, tmdb_id):
            raise TMDBError("tmdb is down")

    worker = Worker(store, BoomTMDBClient(), FakeQBTClient())

    with caplog.at_level("ERROR", logger="app.worker"):
        created = worker.check_show(show)

    assert created == 0
    assert store.get_show(show.id).last_checked_at is None


def test_check_all_watching_shows_only_checks_watching_shows():
    store = RequestStore(":memory:")
    watching = store.create_show(tmdb_id=1, title="A")
    paused = store.create_show(tmdb_id=2, title="B")
    store.update_show_status(paused.id, "paused")
    tmdb = FakeTMDBClient(
        show={"id": 1, "name": "A", "original_name": "A", "number_of_seasons": 1},
        season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}]},
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    asyncio.run(worker._check_all_watching_shows())

    assert store.get_show(watching.id).last_checked_at is not None
    assert store.get_show(paused.id).last_checked_at is None


# ---------------------------------------------------------------------------
# _recheck_is_due — pure scheduling logic (Stage 12.x)
# ---------------------------------------------------------------------------


def _episode_row(**overrides):
    base = dict(
        id=1,
        show_id=1,
        season_number=1,
        episode_number=1,
        request_id=1,
        created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        recheck_count=0,
        last_rechecked_at=None,
    )
    base.update(overrides)
    return ShowEpisodeRow(**base)


def _tv_settings(**overrides):
    base = dict(
        show_check_interval_hours=6,
        episode_recheck_enabled=True,
        episode_recheck_interval_hours=1,
        episode_recheck_max_attempts=3,
    )
    base.update(overrides)
    return TVScheduleSettings(**base)


def test_recheck_is_due_once_interval_has_elapsed_since_creation():
    row = _episode_row(created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    assert Worker._recheck_is_due(row, _tv_settings(episode_recheck_interval_hours=1)) is True


def test_recheck_not_due_before_interval_elapses():
    row = _episode_row(created_at=datetime.now(timezone.utc).isoformat())
    assert Worker._recheck_is_due(row, _tv_settings(episode_recheck_interval_hours=1)) is False


def test_recheck_uses_last_rechecked_at_over_created_at_when_present():
    row = _episode_row(
        created_at=(datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
        last_rechecked_at=datetime.now(timezone.utc).isoformat(),
    )
    assert Worker._recheck_is_due(row, _tv_settings(episode_recheck_interval_hours=1)) is False


def test_recheck_not_due_once_max_attempts_reached():
    row = _episode_row(recheck_count=3, created_at=(datetime.now(timezone.utc) - timedelta(hours=100)).isoformat())
    assert Worker._recheck_is_due(row, _tv_settings(episode_recheck_max_attempts=3)) is False


def test_recheck_always_due_when_max_attempts_is_zero_no_matter_the_count():
    row = _episode_row(recheck_count=999, created_at=(datetime.now(timezone.utc) - timedelta(hours=100)).isoformat())
    assert Worker._recheck_is_due(row, _tv_settings(episode_recheck_max_attempts=0)) is True


# ---------------------------------------------------------------------------
# _run_due_rechecks — end-to-end recheck behavior (Stage 12.x)
# ---------------------------------------------------------------------------


def test_run_due_rechecks_noop_when_disabled():
    store = RequestStore(":memory:")  # episode_recheck_enabled defaults to False
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    old_request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=3
    )
    store.update_status(old_request.id, "no qualifying results")
    store.add_show_episode(show.id, 1, 3, old_request.id)
    _backdate_show_episode(store, show.id, 1, 3, hours_ago=100)
    qbt = FakeQBTClient(results_by_variant={"Lanterns S01E03": [_episode_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 0
    assert qbt.added == []


def test_run_due_rechecks_retries_a_no_qualifying_results_episode_and_succeeds():
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True, "episode_recheck_interval_hours": 1})
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    old_request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=3
    )
    store.update_status(old_request.id, "no qualifying results")
    store.add_show_episode(show.id, 1, 3, old_request.id)
    _backdate_show_episode(store, show.id, 1, 3, hours_ago=2)
    qbt = FakeQBTClient(
        results_by_variant={"Lanterns S01E03": [_episode_result(fileName="Lanterns.S01E03.2160p.WEB-DL.mkv")]}
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 1
    assert episode_row.request_id != old_request.id
    new_request = store.get_request(episode_row.request_id)
    assert new_request.status == "downloading"
    assert new_request.result["torrent_hash"] == "bbbb"
    assert store.get_request(old_request.id).status == "no qualifying results"  # old row kept, untouched


def test_run_due_rechecks_records_attempt_when_retry_still_finds_nothing():
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True, "episode_recheck_interval_hours": 1})
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    old_request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=3
    )
    store.update_status(old_request.id, "no qualifying results")
    store.add_show_episode(show.id, 1, 3, old_request.id)
    _backdate_show_episode(store, show.id, 1, 3, hours_ago=2)
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())  # no results configured

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 1
    assert episode_row.request_id == old_request.id


def test_run_due_rechecks_skips_an_actively_downloading_episode_without_burning_an_attempt():
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True, "episode_recheck_interval_hours": 1})
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=3
    )
    store.update_status(request.id, "downloading", result={"torrent_hash": "aaaa"})
    store.add_show_episode(show.id, 1, 3, request.id)
    _backdate_show_episode(store, show.id, 1, 3, hours_ago=2)
    qbt = FakeQBTClient()
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 0
    assert qbt.added == []


def test_run_due_rechecks_skips_a_cancelled_episode_without_burning_an_attempt():
    """A deliberate cancel (whether via the API or an outside deletion)
    must not be auto-resurrected by the recheck loop."""
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True, "episode_recheck_interval_hours": 1})
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=3
    )
    store.update_status(request.id, "cancelled")
    store.add_show_episode(show.id, 1, 3, request.id)
    _backdate_show_episode(store, show.id, 1, 3, hours_ago=2)
    qbt = FakeQBTClient(results_by_variant={"Lanterns S01E03": [_episode_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 0
    assert qbt.added == []


def test_run_due_rechecks_upgrades_a_complete_episode_when_a_better_release_appears():
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True, "episode_recheck_interval_hours": 1})
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    old_request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(old_request.id, "complete", result={"torrent_hash": "aaaa", "score": {"composite": 100}})
    store.add_show_episode(show.id, 1, 1, old_request.id)
    _backdate_show_episode(store, show.id, 1, 1, hours_ago=2)
    qbt = FakeQBTClient(
        results_by_variant={"Lanterns S01E01": [_episode_result()]},
        torrent_states={"aaaa": {"progress": 1.0}},  # the old torrent, still present in qBittorrent
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 1
    assert episode_row.request_id != old_request.id
    new_request = store.get_request(episode_row.request_id)
    assert new_request.status == "downloading"
    assert new_request.result["replaces_torrent_hash"] == "aaaa"
    assert store.get_request(old_request.id).status == "complete"  # old row kept, untouched for now


def test_run_due_rechecks_does_not_replace_when_nothing_beats_the_current_score():
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True, "episode_recheck_interval_hours": 1})
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    old_request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(old_request.id, "complete", result={"torrent_hash": "aaaa", "score": {"composite": 999999}})
    store.add_show_episode(show.id, 1, 1, old_request.id)
    _backdate_show_episode(store, show.id, 1, 1, hours_ago=2)
    qbt = FakeQBTClient(results_by_variant={"Lanterns S01E01": [_episode_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 1
    assert episode_row.request_id == old_request.id
    assert qbt.added == []


def test_run_due_rechecks_does_not_replace_when_nothing_is_found_at_all():
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True, "episode_recheck_interval_hours": 1})
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    old_request = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(old_request.id, "complete", result={"torrent_hash": "aaaa", "score": {"composite": 100}})
    store.add_show_episode(show.id, 1, 1, old_request.id)
    _backdate_show_episode(store, show.id, 1, 1, hours_ago=2)
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())  # no results at all

    asyncio.run(worker._run_due_rechecks())

    [episode_row] = store.list_show_episodes(show.id)
    assert episode_row.recheck_count == 1
    assert episode_row.request_id == old_request.id


# ---------------------------------------------------------------------------
# _organize_and_complete_episode — cleaning up a superseded torrent after a
# quality-upgrade recheck (Stage 12.x)
# ---------------------------------------------------------------------------


def test_organize_and_complete_episode_cleans_up_superseded_torrent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    source = tmp_path / "downloads" / "Lanterns.S01E01.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"data")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "bbbb", "replaces_torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={
            "bbbb": {"progress": 1.0, "save_path": str(source.parent)},
            "aaaa": {"progress": 1.0},  # the superseded torrent, still present
        },
        torrent_files={"bbbb": [{"name": source.name, "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "complete"
    assert ("aaaa", True) in qbt.deleted


def test_organize_and_complete_episode_skips_cleanup_when_superseded_torrent_already_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"data")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "bbbb", "replaces_torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"bbbb": {"progress": 1.0, "save_path": str(source.parent)}},
        torrent_files={"bbbb": [{"name": source.name, "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "complete"
    assert qbt.deleted == []


def test_organize_and_complete_episode_still_completes_if_cleanup_of_old_torrent_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"data")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "bbbb", "replaces_torrent_hash": "aaaa"})

    class BoomOnDeleteQBTClient(FakeQBTClient):
        def delete_torrent(self, torrent_hash, delete_files=True):
            raise RuntimeError("qbittorrent unreachable")

    qbt = BoomOnDeleteQBTClient(
        torrent_states={
            "bbbb": {"progress": 1.0, "save_path": str(source.parent)},
            "aaaa": {"progress": 1.0},
        },
        torrent_files={"bbbb": [{"name": source.name, "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "complete"  # cleanup failure doesn't undo this
