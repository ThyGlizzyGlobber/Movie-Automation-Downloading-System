import asyncio
import itertools
import os
import threading
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import config
from app.db import RequestStore, ShowEpisodeRow
from app.qbt import QBTError
from app.tv_settings import TVScheduleSettings
from app.tv_resolve import resolve_show
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
    # Stage 13.x's post-organize source cleanup schedules its next attempt
    # this far in the future — zero it out so a freshly-organized row is
    # immediately due for `_run_due_source_cleanups()` rather than tests
    # needing to wait out a real delay.
    monkeypatch.setattr(config, "SOURCE_CLEANUP_DELAY_SECONDS", 0)

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
    "poster_path": "/lanterns.jpg",
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

    def list_torrents(self):
        return [{"hash": h, **state} for h, state in self._torrent_states.items()]

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


def test_run_one_movie_without_override_rejects_release_below_global_floor(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "2160p")
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileName="Dune.Part.Two.2024.1080p.WEB.mkv")]}
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "no qualifying results"


def test_run_one_movie_resolution_override_allows_release_below_global_floor():
    store = RequestStore(":memory:")
    row = store.create_request(
        tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None, min_resolution="1080p"
    )
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileName="Dune.Part.Two.2024.1080p.WEB.mkv")]}
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "downloading"


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


def test_check_downloading_marks_complete_when_progress_reaches_one(tmp_path, monkeypatch, caplog):
    """A movie row now also has to clear the organize step (see the
    dedicated _organize_and_complete_movie tests below) — previously
    reaching progress>=1 alone was enough, back when movies had no
    automatic organize step at all."""
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "downloads" / "Dune.Part.Two.2024.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"data")

    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": str(source.parent)}},
        torrent_files={"aaaa": [{"name": source.name, "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    with caplog.at_level("INFO", logger="app.worker"):
        asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "complete"
    assert any("downloading -> complete" in r.message for r in caplog.records)


def test_check_downloading_adopts_an_untracked_torrent_by_release_name():
    """A row whose add never pinned down a hash (live: a Ted season pack
    stuck at downloading) picks up the torrent carrying its release name,
    skipping one another request already tracks."""
    store = RequestStore(":memory:")
    other = store.create_request(tmdb_id=1, title="Other", release_year=2024, query=None)
    store.update_status(other.id, "downloading", result={"torrent_hash": "bbbb"})
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    winner = {"fileName": "Dune.Part.Two.2024.2160p.REMUX.mkv"}
    store.update_status(row.id, "downloading", result={"torrent_hash": None, "winner": winner})
    qbt = FakeQBTClient(
        torrent_states={
            "bbbb": {"progress": 0.2, "name": "Dune.Part.Two.2024.2160p.REMUX"},
            "cccc": {"progress": 0.4, "name": "Dune.Part.Two.2024.2160p.REMUX"},
            "dddd": {"progress": 0.9, "name": "Something.Else.1080p"},
        }
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    adopted = store.get_request(row.id)
    assert adopted.status == "downloading"
    assert adopted.result["torrent_hash"] == "cccc"
    assert adopted.result["winner"] == winner
    assert adopted.download_progress == 0.4


def test_check_downloading_fails_an_untracked_row_once_the_grace_period_is_up():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": None, "winner": {"fileName": "Dune.2160p"}})
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._check_downloading())
    assert store.get_request(row.id).status == "downloading"  # still within the grace period

    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store._conn.execute("UPDATE requests SET updated_at = ? WHERE id = ?", (stale, row.id))
    asyncio.run(worker._check_downloading())
    failed = store.get_request(row.id)
    assert failed.status == "failed"
    assert failed.error_message == "Lost track of this download in qBittorrent"


def test_episode_rows_without_a_poster_take_the_shows():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns", poster_path="/lanterns.jpg")
    episode = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1)
    pack = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    assert episode.poster_path == "/lanterns.jpg"
    assert pack.poster_path == "/lanterns.jpg"


def test_startup_backfills_missing_tv_posters(tmp_path):
    path = tmp_path / "p.db"
    store = RequestStore(path)
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    good = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1, poster_path="/l.jpg")
    bare = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=2)
    assert bare.poster_path is None
    store._conn.close()

    reopened = RequestStore(path)
    assert reopened.get_request(bare.id).poster_path == "/l.jpg"
    assert reopened.get_request(good.id).poster_path == "/l.jpg"


def test_check_downloading_leaves_in_progress_torrent_alone():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 0.4}})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "downloading"


def test_check_downloading_persists_live_progress_fraction():
    """Frontend migration Part J2 — the same `progress` value this branch
    was already reading (to decide it's not >= 1 yet) is now also
    persisted, so the Requests queue can show a real progress bar."""
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 0.42}})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).download_progress == pytest.approx(0.42)


def test_check_downloading_progress_updates_on_repeated_polls():
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 0.1}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    asyncio.run(worker._check_downloading())
    assert store.get_request(row.id).download_progress == pytest.approx(0.1)

    qbt._torrent_states["aaaa"]["progress"] = 0.75
    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).download_progress == pytest.approx(0.75)


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
    monkeypatch.setattr("app.worker.plex.has_in_library", lambda store, title, year, tmdb_id=None: True)
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "complete"


def test_check_downloading_marks_cancelled_with_plex_checked_message_when_not_found(monkeypatch):
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={})
    monkeypatch.setattr("app.worker.plex.has_in_library", lambda store, title, year, tmdb_id=None: False)
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


def test_run_one_episode_honours_the_row_quality_floor():
    """An episode row created for a show with its own floor (the Anything
    profile on an older show) carries that floor, so a 720p-only release
    is accepted where the household's 2160p default would refuse it."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=4, min_resolution="480p"
    )
    qbt = FakeQBTClient(results_by_variant={"Lanterns S01E04": [_episode_result(fileName="Lanterns.S01E04.720p.WEB-DL.mkv")]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "downloading"
    assert len(qbt.added) == 1


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


def test_check_downloading_purges_episode_torrent_with_no_video_file_at_all(tmp_path, monkeypatch):
    """Same fake-release protection as the movie-level test, for a single
    episode request: no real video file anywhere in the torrent -> purge
    it and its files, land on "cancelled", not "downloaded, not filed"."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": str(tmp_path)}},
        torrent_files={
            "aaaa": [
                {"name": "release.exe", "size": 964_900_000},
                {"name": "site.jpg", "size": 38_000},
                {"name": "readme.txt", "size": 848},
            ]
        },
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "cancelled"
    assert reloaded.error_message is not None
    assert qbt.deleted == [("aaaa", True)]


def test_check_downloading_translates_qbit_container_path_before_organizing(tmp_path, monkeypatch):
    """The real bug this reproduces: qBittorrent and this backend run as
    separate containers, each bind-mounting the identical host folder
    under a different internal path. Without translation, the hardlink
    step tries to read a source path that doesn't exist in this
    container's own filesystem and fails. QBIT_TV_SAVE_PATH configured
    correctly should make organizing succeed even though qBittorrent's
    own reported save_path never matches TV_LIBRARY_ROOT as a plain
    string."""
    library_root = tmp_path / "library"
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library_root)
    monkeypatch.setattr(config, "QBIT_TV_SAVE_PATH", "/media/TV Shows")
    real_source_dir = library_root / "Lanterns S01E01"
    real_source_dir.mkdir(parents=True)
    (real_source_dir / "Lanterns.S01E01.mkv").write_bytes(b"data")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": "/media/TV Shows/Lanterns S01E01"}},
        torrent_files={"aaaa": [{"name": "Lanterns.S01E01.mkv", "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "complete"
    target = library_root / "Lanterns (2026) {tmdb-95350}" / "Season 01" / "Lanterns - s01e01.mkv"
    assert target.exists()


def test_check_downloading_organizes_movie_and_marks_complete(tmp_path, monkeypatch):
    """Movies previously had no automatic organize step at all —
    organize_movie() was CLI-only. This is the movie equivalent of
    test_check_downloading_organizes_episode_and_marks_complete above."""
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "downloads" / "Dune.Part.Two.2024.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"data")

    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": str(source.parent)}},
        torrent_files={"aaaa": [{"name": source.name, "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "complete"
    target = tmp_path / "library" / "Dune Part Two (2024) {tmdb-693134}" / "Dune.Part.Two.2024.mkv"
    assert target.exists()


def test_check_downloading_marks_movie_downloaded_not_filed_when_organize_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})  # no save_path -> MediaOrganizerError
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloaded, not filed"
    assert reloaded.error_message is not None


def test_check_downloading_purges_movie_torrent_with_no_video_file_at_all(tmp_path, monkeypatch):
    """Real-world case, confirmed live 2026-09-14: a torrent whose release
    name looked completely clean got added, but contained no actual video
    file — just filler (.txt, .jpg) and a disguised .exe padded to look
    like real content. Rather than sitting forever as "downloaded, not
    filed" with the payload still on disk, this torrent (and its files)
    must be deleted outright and the request marked "cancelled"."""
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": str(tmp_path)}},
        torrent_files={
            "aaaa": [
                {"name": "release.exe", "size": 964_900_000},
                {"name": "site.jpg", "size": 38_000},
                {"name": "readme.txt", "size": 848},
            ]
        },
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "cancelled"
    assert reloaded.error_message is not None
    assert qbt.deleted == [("aaaa", True)]


def test_check_downloading_translates_qbit_container_path_before_organizing_movie(tmp_path, monkeypatch):
    """Same real-world container-path mismatch as TV's — movies get their
    own QBIT_MOVIE_SAVE_PATH/MOVIE_LIBRARY_ROOT translation."""
    library_root = tmp_path / "library"
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", library_root)
    monkeypatch.setattr(config, "QBIT_MOVIE_SAVE_PATH", "/media/Movies")
    real_source_dir = library_root / "Dune Part Two"
    real_source_dir.mkdir(parents=True)
    (real_source_dir / "Dune.Part.Two.2024.mkv").write_bytes(b"data")

    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": "/media/Movies/Dune Part Two"}},
        torrent_files={"aaaa": [{"name": "Dune.Part.Two.2024.mkv", "size": 4}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "complete"
    target = library_root / "Dune Part Two (2024) {tmdb-693134}" / "Dune.Part.Two.2024.mkv"
    assert target.exists()


def test_check_downloading_marks_episode_downloaded_not_filed_when_source_path_does_not_exist(
    tmp_path, monkeypatch
):
    """Regression test for the real live bug: a source path that doesn't
    exist in this container's own filesystem (the untranslated-path
    mismatch, or any other missing-file case) must not leave the request
    stuck at "downloading" forever — the hardlink/copy step raises a raw
    OSError, not a MediaOrganizerError, and that used to propagate
    uncaught out of _organize_and_complete_episode, silently retried
    every poll cycle with the row never advancing."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    # QBIT_TV_SAVE_PATH deliberately left unset (or non-matching), so the
    # reported save_path is used as-is — and doesn't exist on disk here,
    # exactly like the real NAS's qBittorrent-container-only path.

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(row.id, "downloading", result={"torrent_hash": "aaaa"})
    qbt = FakeQBTClient(
        torrent_states={"aaaa": {"progress": 1.0, "save_path": "/media/TV Shows/Lanterns S01E01"}},
        torrent_files={"aaaa": [{"name": "Lanterns.S01E01.mkv", "size": 4}]},
    )
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
# _run_one / _check_downloading — Stage 13 bulk pack requests
# ---------------------------------------------------------------------------


def _pack_result(**overrides):
    base = {
        "engineName": "piratebay",
        "fileName": "Lanterns.S01.2160p.WEB-DL.mkv",
        "fileUrl": "magnet:?xt=urn:btih:CCCC",
        "fileSize": 40_000_000_000,
        "nbSeeders": 100,
    }
    base.update(overrides)
    return base


def test_run_one_downloads_a_season_pack_request():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    qbt = FakeQBTClient(results_by_variant={"Lanterns Season 01": [_pack_result()]})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloading"
    assert reloaded.result["torrent_hash"] == "cccc"


# -- frontend migration Part J4: a bulk season/series download's own
#    resolution picker — same per-request floor override movie requests
#    already had, now wired up for pack requests too. --


def test_run_one_pack_without_override_rejects_release_below_global_floor(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "2160p")
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    qbt = FakeQBTClient(
        results_by_variant={"Lanterns Season 01": [_pack_result(fileName="Lanterns.S01.1080p.WEB-DL.mkv")]}
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "no qualifying results"


def test_run_one_pack_resolution_override_allows_release_below_global_floor():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, min_resolution="1080p"
    )
    qbt = FakeQBTClient(
        results_by_variant={"Lanterns Season 01": [_pack_result(fileName="Lanterns.S01.1080p.WEB-DL.mkv")]}
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "downloading"


def test_run_one_downloads_a_complete_series_request():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None)
    qbt = FakeQBTClient(
        results_by_variant={
            "Lanterns complete series": [_pack_result(fileName="Lanterns.Complete.Series.2160p.mkv")]
        }
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "downloading"


def test_run_one_marks_pack_no_qualifying_results():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).status == "no qualifying results"


_TWO_AIRED_ONE_FUTURE = [
    {"episode_number": 1, "air_date": "2020-01-01"},
    {"episode_number": 2, "air_date": "2020-01-08"},
    {"episode_number": 3, "air_date": "2999-01-01"},
]


def test_pack_with_no_pack_at_all_falls_back_to_one_request_per_aired_episode():
    """Shows that only ever circulate as single episodes: "Request season
    1" must still get season 1. The episode rows inherit the pack's floor;
    the pack row explains what happened."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, min_resolution="480p"
    )
    tmdb = FakeTMDBClient(season_episodes={1: _TWO_AIRED_ONE_FUTURE})
    worker = Worker(store, tmdb, FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    pack = store.get_request(row.id)
    assert pack.status == "no qualifying results"
    assert pack.error_message == "No season pack found, so its 2 episodes were requested one by one."
    episodes = [r for r in store.list_requests() if r.media_type == "episode"]
    assert sorted((r.season_number, r.episode_number) for r in episodes) == [(1, 1), (1, 2)]
    assert {r.min_resolution for r in episodes} == {"480p"}
    assert {r.status for r in episodes} == {"queued"}
    assert sorted(worker.queue.get_nowait() for _ in range(2)) == sorted(r.id for r in episodes)
    assert store.has_show_episode(show.id, 1, 1) and store.has_show_episode(show.id, 1, 2)
    assert not store.has_show_episode(show.id, 1, 3)


def test_check_show_stops_following_a_finished_show_once_nothing_is_left():
    """Shows you follow is for series still going: an ended show whose
    episodes are all handled drops off the list on its next check."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1)
    store.update_status(row.id, "complete")
    store.add_show_episode(show.id, 1, 1, row.id)
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 1, "status": "Ended"},
        season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}]},
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    assert worker.check_show(show) == 0
    after = store.get_show(show.id)
    assert after.status == "paused"
    assert after.tmdb_status == "Ended"


def test_check_show_keeps_following_a_returning_show_with_nothing_new():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1)
    store.update_status(row.id, "complete")
    store.add_show_episode(show.id, 1, 1, row.id)
    tmdb = FakeTMDBClient(show={**SHOW, "number_of_seasons": 1, "status": "Returning Series"}, season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}]})
    worker = Worker(store, tmdb, FakeQBTClient())

    assert worker.check_show(show) == 0
    after = store.get_show(show.id)
    assert after.status == "watching"
    assert after.tmdb_status == "Returning Series"


def test_check_show_fetches_a_followed_season_finale_as_one_episode_not_a_pack():
    """Reacher (2026-09-17): episodes 1–7 arrived week by week, the
    finale aired and the check queued the whole season pack. A season the
    household already has part of finishes one episode at a time."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    for ep in (1, 2):
        row = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=ep)
        store.update_status(row.id, "complete")
        store.add_show_episode(show.id, 1, ep, row.id)
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 1},
        season_episodes={
            1: [
                {"episode_number": 1, "air_date": "2020-01-01"},
                {"episode_number": 2, "air_date": "2020-01-08"},
                {"episode_number": 3, "air_date": "2020-01-15"},  # the finale, just aired
            ]
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    assert worker.check_show(show) == 1
    new_rows = [r for r in store.list_requests() if r.status == "queued"]
    assert [(r.media_type, r.season_number, r.episode_number) for r in new_rows] == [("episode", 1, 3)]


def test_check_show_ended_show_with_episodes_in_the_ledger_finishes_by_episode():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1)
    store.update_status(row.id, "complete")
    store.add_show_episode(show.id, 1, 1, row.id)
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 1, "status": "Ended"},
        season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}, {"episode_number": 2, "air_date": "2020-01-08"}]},
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    assert worker.check_show(show) == 1
    new_rows = [r for r in store.list_requests() if r.status == "queued"]
    assert [(r.media_type, r.season_number, r.episode_number) for r in new_rows] == [("episode", 1, 2)]


def test_check_show_leaves_an_airing_season_alone_while_a_series_pack_is_on_the_way():
    """Following a show right after "Add all to Plex": the scheduler must
    not also ask for the airing season's episodes one by one while the
    pack that covers them is still downloading."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    in_flight = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None)
    store.update_status(in_flight.id, "downloading")
    tmdb = FakeTMDBClient(show={**SHOW, "number_of_seasons": 1}, season_episodes={1: _TWO_AIRED_ONE_FUTURE})
    worker = Worker(store, tmdb, FakeQBTClient())

    assert worker.check_show(show) == 0
    assert [r for r in store.list_requests() if r.media_type == "episode"] == []

    # Once the pack is done (or gone), the usual per-episode catch-up resumes.
    store.update_status(in_flight.id, "cancelled")
    assert worker.check_show(show) == 2


def test_series_pack_fallback_asks_for_each_season_not_each_episode():
    """PEN15 (2026-09-17): no series pack existed but every season had a
    well-seeded pack. A failed series request steps down to one request
    per season; a fully handled season and an unaired one are skipped."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    earlier = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1)
    store.add_show_episode(show.id, 1, 1, earlier.id)
    row = store.create_pack_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None, requested_by_username="bejay"
    )
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 3},
        season_episodes={
            1: [{"episode_number": 1, "air_date": "2020-01-01"}],  # fully handled already
            2: _TWO_AIRED_ONE_FUTURE,
            3: [{"episode_number": 1, "air_date": "2999-01-01"}],  # nothing aired yet
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    pack = store.get_request(row.id)
    assert pack.status == "no qualifying results"
    assert pack.error_message == "No series pack found, so its 1 season was requested one at a time."
    seasons = [r for r in store.list_requests() if r.media_type == "pack" and r.id != row.id]
    assert [(r.season_number, r.status, r.requested_by_username) for r in seasons] == [(2, "queued", "bejay")]
    assert [r for r in store.list_requests() if r.media_type == "episode" and r.id != earlier.id] == []
    assert worker.queue.get_nowait() == seasons[0].id


def test_series_fallback_message_says_why_when_a_better_season_pack_exists():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None)
    qbt = FakeQBTClient(
        results_by_variant={
            "Lanterns complete series": [_pack_result(fileName="Lanterns.S01-S02.720p.WEB-DL")],
            "Lanterns Season 01": [_pack_result(fileName="Lanterns.S01.2160p.WEB-DL", fileUrl="magnet:?xt=urn:btih:BBBB")],
        }
    )
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 2},
        season_episodes={1: _TWO_AIRED_ONE_FUTURE, 2: [{"episode_number": 1, "air_date": "2021-01-01"}]},
    )
    worker = Worker(store, tmdb, qbt)

    asyncio.run(worker._run_one(row.id))

    pack = store.get_request(row.id)
    assert pack.error_message == "The only whole-series pack is 720p; the seasons come in 2160p, so its 2 seasons were requested one at a time."
    assert sorted(r.season_number for r in store.list_requests() if r.media_type == "pack" and r.id != row.id) == [1, 2]


def test_plex_is_the_verifier_for_the_follow_check(monkeypatch):
    """An episode Plex already holds is marked done and never requested,
    whatever the ledger says."""
    from app import plex as plex_module

    monkeypatch.setattr(plex_module, "plex_show_episodes", lambda store, title, year, tmdb_id=None: {(1, 1)})
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(show={**SHOW, "number_of_seasons": 1}, season_episodes={1: _TWO_AIRED_ONE_FUTURE})
    worker = Worker(store, tmdb, FakeQBTClient())

    assert worker.check_show(show) == 1  # only episode 2 is fetched
    rows = {(r.season_number, r.episode_number): r for r in store.list_requests() if r.media_type == "episode"}
    assert rows[(1, 1)].status == "complete" and rows[(1, 1)].result == {"note": "already on Plex, not downloaded by this app"}
    assert rows[(1, 2)].status == "queued"


def test_plex_is_the_verifier_for_the_season_fallback(monkeypatch):
    from app import plex as plex_module

    monkeypatch.setattr(plex_module, "plex_show_episodes", lambda store, title, year, tmdb_id=None: {(1, 1), (1, 2)})
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None)
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 2},
        season_episodes={1: _TWO_AIRED_ONE_FUTURE, 2: [{"episode_number": 1, "air_date": "2021-01-01"}]},
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    # Season 1 is on Plex, so only season 2 is asked for; the two on-Plex episodes are recorded as done.
    assert [r.season_number for r in store.list_requests() if r.media_type == "pack" and r.id != row.id] == [2]
    assert sorted((r.season_number, r.episode_number) for r in store.list_requests() if r.media_type == "episode" and r.status == "complete") == [(1, 1), (1, 2)]


def test_series_fallback_ignores_episodes_whose_own_attempts_failed():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    for ep in (1, 2):
        old = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=ep)
        store.add_show_episode(show.id, 1, ep, old.id)
        store.update_status(old.id, "no qualifying results")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None)
    tmdb = FakeTMDBClient(show={**SHOW, "number_of_seasons": 1}, season_episodes={1: _TWO_AIRED_ONE_FUTURE})
    worker = Worker(store, tmdb, FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    assert [r.season_number for r in store.list_requests() if r.media_type == "pack" and r.id != row.id] == [1]


def test_series_fallback_ignores_episodes_a_person_cancelled():
    """Ted on the NAS: per-episode rows from an earlier fallback were
    cancelled, but still sat in the ledger, so the season fallback saw
    both seasons as handled and asked for nothing."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    for ep in (1, 2):
        old = store.create_episode_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=ep)
        store.add_show_episode(show.id, 1, ep, old.id)
        store.update_status(old.id, "cancelled")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None)
    tmdb = FakeTMDBClient(show={**SHOW, "number_of_seasons": 1}, season_episodes={1: _TWO_AIRED_ONE_FUTURE})
    worker = Worker(store, tmdb, FakeQBTClient())

    asyncio.run(worker._run_one(row.id))

    assert store.get_request(row.id).error_message == "No series pack found, so its 1 season was requested one at a time."
    assert [r.season_number for r in store.list_requests() if r.media_type == "pack" and r.id != row.id] == [1]


def test_season_pack_from_a_series_fallback_still_drops_to_episodes():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=None)
    tmdb = FakeTMDBClient(show={**SHOW, "number_of_seasons": 1}, season_episodes={1: _TWO_AIRED_ONE_FUTURE})
    worker = Worker(store, tmdb, FakeQBTClient())

    asyncio.run(worker._run_one(row.id))
    season_row = next(r for r in store.list_requests() if r.media_type == "pack" and r.season_number == 1)
    asyncio.run(worker._run_one(season_row.id))

    assert store.get_request(season_row.id).error_message == "No season pack found, so its 2 episodes were requested one by one."
    episodes = [r for r in store.list_requests() if r.media_type == "episode"]
    assert sorted((r.season_number, r.episode_number) for r in episodes) == [(1, 1), (1, 2)]


def test_check_downloading_organizes_pack_and_fans_out_episode_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.mkv").write_bytes(b"ep1")
    (downloads / "Lanterns.S01E02.mkv").write_bytes(b"ep2")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "downloading", result={"torrent_hash": "cccc"})
    qbt = FakeQBTClient(
        torrent_states={"cccc": {"progress": 1.0, "save_path": str(downloads)}},
        torrent_files={
            "cccc": [
                {"name": "Lanterns.S01E01.mkv", "size": 3},
                {"name": "Lanterns.S01E02.mkv", "size": 3},
            ]
        },
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "complete"
    episode_rows = [r for r in store.list_requests() if r.media_type == "episode"]
    assert len(episode_rows) == 2
    assert {(r.season_number, r.episode_number) for r in episode_rows} == {(1, 1), (1, 2)}
    assert all(r.status == "complete" for r in episode_rows)
    assert store.has_show_episode(show.id, 1, 1)
    assert store.has_show_episode(show.id, 1, 2)


def test_check_downloading_pack_skips_episode_already_in_ledger(tmp_path, monkeypatch):
    """A pack overlapping an episode the per-episode scheduler already
    handled must not create a duplicate audit trail for the same episode —
    the ledger, not the pack's own file list, is the source of truth for
    "already handled"."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.mkv").write_bytes(b"ep1")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    existing = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=1
    )
    store.update_status(existing.id, "complete")
    store.add_show_episode(show.id, 1, 1, existing.id)

    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "downloading", result={"torrent_hash": "cccc"})
    qbt = FakeQBTClient(
        torrent_states={"cccc": {"progress": 1.0, "save_path": str(downloads)}},
        torrent_files={"cccc": [{"name": "Lanterns.S01E01.mkv", "size": 3}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    episode_rows = [r for r in store.list_requests() if r.media_type == "episode"]
    assert len(episode_rows) == 1  # no duplicate created
    assert store.get_request(row.id).status == "complete"


def test_check_downloading_marks_pack_downloaded_not_filed_when_organize_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "downloading", result={"torrent_hash": "cccc"})
    qbt = FakeQBTClient(torrent_states={"cccc": {"progress": 1.0}})  # no save_path -> MediaOrganizerError
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloaded, not filed"
    assert reloaded.error_message is not None


def test_check_downloading_purges_pack_torrent_with_no_video_file_at_all(tmp_path, monkeypatch):
    """Same fake-release protection, for a season/complete-series pack
    request: no real video file anywhere in the pack -> purge it and its
    files, land on "cancelled", not "downloaded, not filed"."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "downloading", result={"torrent_hash": "cccc"})
    qbt = FakeQBTClient(
        torrent_states={"cccc": {"progress": 1.0, "save_path": str(tmp_path)}},
        torrent_files={
            "cccc": [
                {"name": "release.exe", "size": 964_900_000},
                {"name": "site.jpg", "size": 38_000},
                {"name": "readme.txt", "size": 848},
            ]
        },
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "cancelled"
    assert reloaded.error_message is not None
    assert qbt.deleted == [("cccc", True)]


def test_check_downloading_marks_pack_downloaded_not_filed_when_source_path_does_not_exist(tmp_path, monkeypatch):
    """Same regression as the episode-level test above, for a pack row:
    a source path that doesn't exist in this container's own filesystem
    (the real qBittorrent-container-path mismatch, unconfigured here)
    raises a raw OSError from organize_pack's hardlink/copy step — must
    land on "downloaded, not filed", not stay stuck at "downloading"."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "downloading", result={"torrent_hash": "cccc"})
    qbt = FakeQBTClient(
        torrent_states={"cccc": {"progress": 1.0, "save_path": "/media/TV Shows/Lanterns S01"}},
        torrent_files={"cccc": [{"name": "Lanterns.S01E01.mkv", "size": 3}]},
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloaded, not filed"
    assert reloaded.error_message is not None


def test_check_downloading_marks_pack_cancelled_when_gone_without_asking_plex(monkeypatch):
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "downloading", result={"torrent_hash": "cccc"})
    qbt = FakeQBTClient(torrent_states={})

    def _boom(*args, **kwargs):
        raise AssertionError("plex.has_in_library should not be called for pack rows")

    monkeypatch.setattr("app.worker.plex.has_in_library", _boom)
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "cancelled"


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
    # Frontend migration Part J1 — carried from the resolved show identity,
    # not left null, on every episode request this scheduler creates.
    assert all(r.poster_path == "/lanterns.jpg" for r in requests)


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


def test_check_show_default_only_checks_the_latest_season_even_with_multiple_seasons():
    """The scheduled-recheck path (full_backfill's default, False) must
    stay exactly as before this stage — only ever the latest season,
    regardless of how many earlier seasons exist or whether they've ever
    been checked. Season 3 (the latest) is kept still-airing here
    specifically so this test isolates "only the latest season" from the
    separate "prefer a pack once a season is complete" behavior, covered
    by its own tests below."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 3},
        season_episodes={
            1: [{"episode_number": 1, "air_date": "2020-01-01"}],
            2: [{"episode_number": 1, "air_date": "2021-01-01"}],
            3: [
                {"episode_number": 1, "air_date": "2022-01-01"},
                {"episode_number": 2, "air_date": "2099-01-01"},  # unaired -> still airing
            ],
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show)

    assert created == 1
    requests = store.list_requests()
    assert {(r.media_type, r.season_number, r.episode_number) for r in requests} == {("episode", 3, 1)}


def test_check_show_full_backfill_bundles_a_complete_unhandled_prefix_into_one_range_pack():
    """A first-time subscribe (api.py's POST /api/shows, full_backfill=True)
    to a show with nothing downloaded at all must grab everything already
    aired across every season, not just the newest — the gap this stage
    fixed. Seasons 1-2 have both finished airing and are both unhandled, so
    they bundle into one season-range pack rather than one search per
    season; season 3 still has an unaired episode, so it stays per-episode
    as before. Enqueue order (and therefore the order the single-worker
    queue processes them in) follows season, ascending."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 3},
        season_episodes={
            1: [
                {"episode_number": 1, "air_date": "2020-01-01"},
                {"episode_number": 2, "air_date": "2020-01-08"},
            ],
            2: [{"episode_number": 1, "air_date": "2021-01-01"}],
            3: [
                {"episode_number": 1, "air_date": "2022-01-01"},
                {"episode_number": 2, "air_date": "2099-01-01"},  # unaired -> season 3 not complete
            ],
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show, full_backfill=True)

    assert created == 2  # one range pack (seasons 1-2) + one episode (season 3 ep 1)
    requests = sorted(store.list_requests(), key=lambda r: r.id)
    assert [(r.media_type, r.season_number, r.season_range_end, r.episode_number) for r in requests] == [
        ("pack", 1, 2, None),
        ("episode", 3, None, 1),
    ]
    assert [worker.queue.get_nowait() for _ in range(2)] == [r.id for r in requests]


def test_check_show_full_backfill_uses_separate_season_packs_when_no_genuine_range_exists():
    """Season 1 is still airing (not complete), so there's no contiguous
    complete-from-1 prefix to bundle — season 2, once it's individually
    reached by the ordinary per-season sweep, still gets its own
    single-season pack exactly as before this range-pack tier existed."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 2},
        season_episodes={
            1: [
                {"episode_number": 1, "air_date": "2020-01-01"},
                {"episode_number": 2, "air_date": "2099-01-01"},  # unaired -> season 1 not complete
            ],
            2: [{"episode_number": 1, "air_date": "2021-01-01"}],  # the "latest" season, also finished
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show, full_backfill=True)

    assert created == 2  # one episode (season 1 ep 1) + one single-season pack (season 2)
    requests = {r.media_type: r for r in store.list_requests()}
    assert requests["episode"].season_number == 1 and requests["episode"].episode_number == 1
    assert requests["pack"].season_number == 2 and requests["pack"].season_range_end is None


def test_check_show_full_backfill_prefers_a_pack_for_a_finished_season():
    """The exact scenario reported: a show not downloaded at all, with
    older seasons unlikely to still have well-seeded individual episode
    releases. A finished season with several unhandled aired episodes
    becomes ONE pack request, not one per episode."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Reacher")
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 2},
        season_episodes={
            1: [{"episode_number": n, "air_date": "2022-01-01"} for n in range(1, 9)],  # fully aired season
            2: [
                {"episode_number": 1, "air_date": "2023-01-01"},
                {"episode_number": 2, "air_date": "2099-01-01"},  # unaired -> still airing
            ],
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show, full_backfill=True)

    assert created == 2  # one pack for season 1, one episode request for season 2's aired episode
    requests = {r.media_type: r for r in store.list_requests()}
    assert requests["pack"].season_number == 1
    assert requests["episode"].season_number == 2 and requests["episode"].episode_number == 1


def test_check_show_prefers_a_pack_for_a_finished_latest_season_even_without_full_backfill():
    """The retroactive gap this stage closed: a show subscribed while
    still airing, whose current season later finishes, must start getting
    pack-preference for that season on the very next *ordinary* scheduled
    recheck — not only at a first-ever full backfill. season_is_complete()
    is evaluated per-season, not "is this a full backfill", so this falls
    out of the same mechanism full_backfill already used."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 1},
        season_episodes={
            1: [{"episode_number": 1, "air_date": "2020-01-01"}, {"episode_number": 2, "air_date": "2020-01-08"}]
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show)  # full_backfill defaults to False, same as a real scheduled recheck

    assert created == 1
    [request_row] = store.list_requests()
    assert request_row.media_type == "pack"
    assert request_row.season_number == 1
    assert request_row.poster_path == "/lanterns.jpg"


def test_check_show_ended_show_queues_one_complete_series_pack_and_skips_the_season_sweep():
    """A show whose TMDB `status` is Ended/Canceled tries one complete-
    series pack before ever touching per-season logic — and, crucially,
    *instead* of it this call, not in addition to it: racing a series-wide
    search against several per-season ones for the same content would just
    waste bandwidth on whichever one loses."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 3, "status": "Ended"},
        season_episodes={
            1: [{"episode_number": 1, "air_date": "2020-01-01"}],
            2: [{"episode_number": 1, "air_date": "2021-01-01"}],
            3: [{"episode_number": 1, "air_date": "2022-01-01"}],
        },
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show, full_backfill=True)

    assert created == 1
    [request_row] = store.list_requests()
    assert request_row.media_type == "pack"
    assert request_row.season_number is None  # series scope, not any one season
    assert worker.queue.qsize() == 1


def test_check_show_canceled_status_also_triggers_a_complete_series_pack():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        show={**SHOW, "status": "Canceled"}, season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}]}
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    created = worker.check_show(show)

    assert created == 1
    assert store.list_requests()[0].media_type == "pack"


# ---------------------------------------------------------------------------
# _should_attempt_pack — the retry gate a pack attempt (season or complete-
# series scope) has to clear, reusing Stage 12.x's episode-recheck settings
# ---------------------------------------------------------------------------


def _settings(**overrides) -> TVScheduleSettings:
    base = dict(
        show_check_interval_hours=6.0,
        episode_recheck_enabled=False,
        episode_recheck_interval_hours=24.0,
        episode_recheck_max_attempts=0,
        episode_air_buffer_hours=0,
    )
    base.update(overrides)
    return TVScheduleSettings(**base)


def test_should_attempt_pack_true_when_never_tried():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    assert worker._should_attempt_pack(show, 1, _settings()) is True


def test_should_attempt_pack_false_when_already_complete():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "complete")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    assert worker._should_attempt_pack(show, 1, _settings(episode_recheck_enabled=True)) is False


def test_should_attempt_pack_false_when_still_in_flight():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)  # starts "queued"
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    assert worker._should_attempt_pack(show, 1, _settings(episode_recheck_enabled=True)) is False


def test_should_attempt_pack_false_after_failure_when_recheck_disabled():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "no qualifying results")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    assert worker._should_attempt_pack(show, 1, _settings(episode_recheck_enabled=False)) is False


def test_should_attempt_pack_false_after_failure_when_cooldown_not_yet_elapsed():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "failed")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    settings = _settings(episode_recheck_enabled=True, episode_recheck_interval_hours=24.0)
    assert worker._should_attempt_pack(show, 1, settings) is False


def test_should_attempt_pack_true_after_failure_once_cooldown_has_elapsed():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "failed")
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    store._conn.execute("UPDATE requests SET updated_at = ? WHERE id = ?", (old, row.id))
    store._conn.commit()
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    settings = _settings(episode_recheck_enabled=True, episode_recheck_interval_hours=24.0)
    assert worker._should_attempt_pack(show, 1, settings) is True


def test_should_attempt_pack_false_once_max_attempts_reached():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    for _ in range(2):
        row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
        store.update_status(row.id, "failed")
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        store._conn.execute("UPDATE requests SET updated_at = ? WHERE id = ?", (old, row.id))
        store._conn.commit()
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    settings = _settings(episode_recheck_enabled=True, episode_recheck_interval_hours=1.0, episode_recheck_max_attempts=2)
    assert worker._should_attempt_pack(show, 1, settings) is False


def test_should_attempt_pack_zero_max_attempts_means_infinite_retries():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    for _ in range(5):
        row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
        store.update_status(row.id, "failed")
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        store._conn.execute("UPDATE requests SET updated_at = ? WHERE id = ?", (old, row.id))
        store._conn.commit()
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    settings = _settings(episode_recheck_enabled=True, episode_recheck_interval_hours=1.0, episode_recheck_max_attempts=0)
    assert worker._should_attempt_pack(show, 1, settings) is True


def test_should_attempt_pack_series_scope_is_independent_of_season_scope():
    """season_number=None (complete series) and season_number=1 track
    completely separate attempt histories — a failed season-1 pack must
    never block a fresh complete-series attempt, or vice versa."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "complete")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    assert worker._should_attempt_pack(show, None, _settings()) is True


def test_should_attempt_pack_range_scope_is_independent_of_single_season_scope():
    """A season-range attempt starting at season 1 and a plain single-
    season-1 attempt must track independent histories too, even though
    they share season_number=1 — only season_range_end distinguishes them."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "complete")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    assert worker._should_attempt_pack(show, 1, _settings(), season_range_end=3) is True


# ---------------------------------------------------------------------------
# _detect_complete_unhandled_prefix — Stage 14.x's season-range detection
# ---------------------------------------------------------------------------


def test_detect_complete_unhandled_prefix_stops_at_first_still_airing_season():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Reacher")
    tmdb = FakeTMDBClient(
        season_episodes={
            1: [{"episode_number": 1, "air_date": "2022-01-01"}],
            2: [{"episode_number": 1, "air_date": "2023-01-01"}],
            3: [
                {"episode_number": 1, "air_date": "2024-01-01"},
                {"episode_number": 2, "air_date": "2099-01-01"},  # unaired -> season 3 not complete
            ],
        }
    )
    worker = Worker(store, tmdb, FakeQBTClient())
    identity = resolve_show(95350, tmdb)

    assert worker._detect_complete_unhandled_prefix(show, identity, latest_season=4) == 2


def test_detect_complete_unhandled_prefix_zero_when_season_one_already_handled():
    """A range should bundle from season 1 — if season 1 is already
    handled some other way, there's no point trying to bundle it into a
    range, so the prefix walk stops immediately."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Reacher")
    store.add_show_episode(show.id, 1, 1, request_id=1)
    tmdb = FakeTMDBClient(season_episodes={1: [{"episode_number": 1, "air_date": "2022-01-01"}]})
    worker = Worker(store, tmdb, FakeQBTClient())
    identity = resolve_show(95350, tmdb)

    assert worker._detect_complete_unhandled_prefix(show, identity, latest_season=2) == 0


def test_detect_complete_unhandled_prefix_zero_when_season_one_still_airing():
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Reacher")
    tmdb = FakeTMDBClient(
        season_episodes={1: [{"episode_number": 1, "air_date": "2099-01-01"}]}  # unaired
    )
    worker = Worker(store, tmdb, FakeQBTClient())
    identity = resolve_show(95350, tmdb)

    assert worker._detect_complete_unhandled_prefix(show, identity, latest_season=2) == 0


def test_check_show_does_not_spam_a_failed_pack_when_recheck_is_disabled():
    """The exact regression this whole gating mechanism exists to prevent:
    calling check_show() again for a show whose only season is finished
    and whose one pack attempt already failed must not queue a second one
    while auto-recheck is off (the default)."""
    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 1},
        season_episodes={1: [{"episode_number": 1, "air_date": "2020-01-01"}]},
    )
    worker = Worker(store, tmdb, FakeQBTClient())

    first = worker.check_show(show)
    assert first == 1
    [pack_row] = store.list_requests()
    store.update_status(pack_row.id, "no qualifying results")  # simulate the pack search coming back empty

    second = worker.check_show(show)

    assert second == 0
    assert len(store.list_requests()) == 1  # no second pack queued


def test_check_show_full_backfill_respects_disk_and_ledger_per_season(tmp_path, monkeypatch):
    """The same "already on disk" / "already in the ledger" skip logic
    check_show() always had must still apply independently within each
    season a full backfill sweeps, not just the latest."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    (tmp_path / "Lanterns.S01E01.2160p.WEB-DL.mkv").write_bytes(b"data")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    store.add_show_episode(show.id, 2, 1, request_id=1)  # already handled, e.g. a prior partial backfill
    tmdb = FakeTMDBClient(
        show={**SHOW, "number_of_seasons": 2},
        season_episodes={
            1: [{"episode_number": 1, "air_date": "2020-01-01"}],
            2: [{"episode_number": 1, "air_date": "2021-01-01"}],
        },
    )
    qbt = FakeQBTClient()
    worker = Worker(store, tmdb, qbt)

    created = worker.check_show(show, full_backfill=True)

    assert created == 0  # S01E01 found on disk, S02E01 already in the ledger
    assert qbt.added == []
    [request_row] = store.list_requests()
    assert (request_row.season_number, request_row.episode_number) == (1, 1)
    assert request_row.status == "complete"


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
        recheck_opted_in=True,
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
        episode_air_buffer_hours=0,
    )
    base.update(overrides)
    return TVScheduleSettings(**base)


def test_recheck_skips_an_episode_claimed_while_the_setting_was_off():
    """Turning "keep looking for missing or better copies" on says what to
    do next; it is not a licence to go back over a library that was
    downloaded while it was off. Those episodes have never been
    rechecked, so their interval is measured from a `created_at` long
    past — without this they would all come due the instant it is
    enabled, which is precisely the flood this guards against."""
    row = _episode_row(recheck_opted_in=False)

    assert Worker._recheck_is_due(row, _tv_settings(episode_recheck_interval_hours=1)) is False
    # And the same episode claimed with the setting on is due as ever.
    assert Worker._recheck_is_due(_episode_row(), _tv_settings(episode_recheck_interval_hours=1)) is True


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

    async def _run():
        await worker._check_downloading()
        await worker._run_due_source_cleanups()

    asyncio.run(_run())

    assert store.get_request(row.id).status == "complete"
    assert ("aaaa", True) in qbt.deleted


def test_organize_and_complete_episode_skips_cleanup_when_superseded_torrent_already_gone(tmp_path, monkeypatch):
    """The *superseded* ("aaaa") torrent is already gone, so its cleanup
    must not attempt a delete — unrelated to (and not to be confused
    with) Stage 13.x's separate post-organize source cleanup of the
    *current* ("bbbb") torrent, which is expected to still happen once
    the sweep below runs."""
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

    async def _run():
        await worker._check_downloading()
        await worker._run_due_source_cleanups()

    asyncio.run(_run())

    assert store.get_request(row.id).status == "complete"
    assert ("aaaa", True) not in qbt.deleted


def test_organize_and_complete_episode_still_completes_if_cleanup_of_old_torrent_fails(tmp_path, monkeypatch):
    """Organizing succeeds and earns "complete" regardless of what happens
    to cleanup afterward — and, since this session's durability fix, a
    failed cleanup attempt is retried on the next sweep rather than lost:
    once qBittorrent stops erroring, a later sweep picks up exactly where
    the failed one left off and finishes the job."""
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
        boom = True

        def delete_torrent(self, torrent_hash, delete_files=True):
            if self.boom:
                raise RuntimeError("qbittorrent unreachable")
            super().delete_torrent(torrent_hash, delete_files)

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

    asyncio.run(worker._run_due_source_cleanups())
    reloaded = store.get_request(row.id)
    assert reloaded.source_cleanup_status == "pending"  # not lost — still owed, will retry
    assert qbt.deleted == []

    qbt.boom = False
    asyncio.run(worker._run_due_source_cleanups())
    assert store.get_request(row.id).source_cleanup_status == "done"
    assert ("bbbb", True) in qbt.deleted
    assert ("aaaa", True) in qbt.deleted


# ---------------------------------------------------------------------------
# Stage 13.x: post-organize source cleanup — at the user's explicit
# request, the *current* torrent (not just a superseded one) gets removed
# from qBittorrent a short delay after its file(s) are organized, once a
# final check confirms the organized copy is genuinely still in place.
# ---------------------------------------------------------------------------


def test_check_downloading_schedules_source_cleanup_after_organizing_episode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
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

    async def _run():
        await worker._check_downloading()
        await worker._run_due_source_cleanups()

    asyncio.run(_run())

    assert store.get_request(row.id).status == "complete"
    assert ("aaaa", True) in qbt.deleted


def _pending_cleanup_row(
    store, torrent_hash: str, organized_paths: list, pending_hashes: list, superseded_paths: list | None = None
) -> None:
    """Builds a real request row already past organizing, sitting in
    `source_cleanup_status = 'pending'` — the same state `mark_organized`
    leaves a row in — so `_attempt_source_cleanup` can be exercised
    directly and realistically, through the actual persisted row shape
    rather than raw positional arguments."""
    row = store.create_request(tmdb_id=1, title="Lanterns S01E01", release_year=None, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": torrent_hash})
    store.mark_organized(
        row.id,
        [str(p) for p in organized_paths],
        pending_hashes,
        "2000-01-01T00:00:00+00:00",
        superseded_paths=[str(p) for p in superseded_paths] if superseded_paths else None,
    )
    return row.id


def test_source_cleanup_defers_while_an_organized_copy_is_unreadable():
    """A safety abort: if the organized copy has vanished the original
    must not be deleted — losing both would be unrecoverable.

    But the first look is not final. A path can be momentarily
    unreadable on a busy NAS, and marking the row done there orphaned
    the source folder permanently and silently. It stays pending for a
    bounded number of sweeps first."""
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [Path("/does/not/exist.mkv")], ["aaaa"])

    asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert qbt.deleted == []
    assert store.get_request(request_id).source_cleanup_status == "pending"
    assert store.get_request(request_id).result["cleanup_verify_attempts"] == 1


def test_source_cleanup_gives_up_loudly_once_the_attempts_run_out(caplog):
    """Bounded, not infinite — and the row says why rather than going
    quiet."""
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [Path("/does/not/exist.mkv")], ["aaaa"])

    with caplog.at_level("WARNING"):
        for _ in range(config.SOURCE_CLEANUP_VERIFY_ATTEMPTS):
            asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert qbt.deleted == []
    assert store.get_request(request_id).source_cleanup_status == "done"
    assert "abandoned after" in caplog.text


def test_source_cleanup_skips_when_organized_paths_is_empty():
    """Python's all([]) is vacuously True — an empty organized_paths list
    must not be mistaken for "nothing to check, safe to proceed", or a
    row with no on-record organized copy would delete its original(s)
    without ever having actually verified anything was placed."""
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [], ["aaaa"])

    asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert qbt.deleted == []
    # Deferred like any other unverifiable row rather than deleting.
    assert store.get_request(request_id).source_cleanup_status == "pending"


def test_source_cleanup_noop_when_torrent_already_gone(tmp_path):
    """The torrent might already be gone by the time this runs (e.g. the
    household's own seeding-time limit beat this to it) — a clean no-op,
    marked done, not an error and not retried."""
    target = tmp_path / "organized.mkv"
    target.write_bytes(b"data")
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={})  # "aaaa" already gone
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [target], ["aaaa"])

    asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert qbt.deleted == []
    assert store.get_request(request_id).source_cleanup_status == "done"


def test_source_cleanup_failure_is_logged_and_stays_pending_for_retry(tmp_path, caplog):
    target = tmp_path / "organized.mkv"
    target.write_bytes(b"data")

    class BoomOnDeleteQBTClient(FakeQBTClient):
        def delete_torrent(self, torrent_hash, delete_files=True):
            raise RuntimeError("qbittorrent unreachable")

    store = RequestStore(":memory:")
    qbt = BoomOnDeleteQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [target], ["aaaa"])

    with caplog.at_level("ERROR", logger="app.worker"):
        asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert "source cleanup failed" in caplog.text
    assert store.get_request(request_id).source_cleanup_status == "pending"  # not "done" — will retry


# -- frontend migration Part K2: "overwrite" redownload also deletes the
#    previously organized file, once the new one is confirmed in place. --


def test_source_cleanup_deletes_superseded_file_once_new_copy_confirmed(tmp_path):
    new_copy = tmp_path / "new.mkv"
    new_copy.write_bytes(b"new")
    old_copy = tmp_path / "old.mkv"
    old_copy.write_bytes(b"old")
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [new_copy], ["aaaa"], superseded_paths=[old_copy])

    asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert not old_copy.exists()
    assert new_copy.exists()  # only the superseded file is touched, never the new one
    assert store.get_request(request_id).source_cleanup_status == "done"


def test_source_cleanup_does_not_delete_superseded_file_when_new_copy_is_missing(tmp_path):
    """Same safety gate as the torrent-cleanup case: if the new organized
    copy isn't genuinely on disk, nothing old gets deleted either —
    losing both would be unrecoverable."""
    old_copy = tmp_path / "old.mkv"
    old_copy.write_bytes(b"old")
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(
        store, "aaaa", [tmp_path / "does-not-exist.mkv"], ["aaaa"], superseded_paths=[old_copy]
    )

    asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert old_copy.exists()  # never touched
    # Deferred, not abandoned: the old copy is the only one left while
    # the new one is unreadable, so it must survive a transient blip too.
    assert store.get_request(request_id).source_cleanup_status == "pending"


def test_source_cleanup_noop_when_superseded_file_already_gone(tmp_path):
    new_copy = tmp_path / "new.mkv"
    new_copy.write_bytes(b"new")
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(
        store, "aaaa", [new_copy], ["aaaa"], superseded_paths=[tmp_path / "already-gone.mkv"]
    )

    asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert store.get_request(request_id).source_cleanup_status == "done"


def test_source_cleanup_superseded_file_failure_is_logged_and_stays_pending_for_retry(tmp_path, caplog, monkeypatch):
    new_copy = tmp_path / "new.mkv"
    new_copy.write_bytes(b"new")
    old_copy = tmp_path / "old.mkv"
    old_copy.write_bytes(b"old")
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [new_copy], ["aaaa"], superseded_paths=[old_copy])

    def boom(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", boom)

    with caplog.at_level("ERROR", logger="app.worker"):
        asyncio.run(worker._attempt_source_cleanup(store.get_request(request_id)))

    assert "superseded-file cleanup failed" in caplog.text
    reloaded = store.get_request(request_id)
    assert reloaded.source_cleanup_status == "pending"  # not "done" — will retry
    assert reloaded.result["superseded_paths"] == [str(old_copy)]


def test_organize_and_complete_movie_schedules_superseded_path_on_overwrite(tmp_path, monkeypatch):
    """End-to-end: a second, 'overwrite'-mode request for the same movie
    finds the first (already-complete) request's organized file and
    schedules it for deletion — the actual mechanism a redownload's
    "Overwrite existing" option relies on."""
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")

    # Both torrents known to qBittorrent from the start — _check_downloading
    # only ever looks at rows actually in 'downloading' status, so the
    # second one just sits unused in qbt until the second request exists.
    first_source = tmp_path / "downloads" / "Dune.Part.Two.2024.WEBRip.mkv"
    first_source.parent.mkdir(parents=True)
    first_source.write_bytes(b"data")
    second_source = tmp_path / "downloads" / "Dune.Part.Two.2024.BluRay.mkv"
    second_source.write_bytes(b"data2")
    qbt = FakeQBTClient(
        torrent_states={
            "aaaa": {"progress": 1.0, "save_path": str(first_source.parent)},
            "bbbb": {"progress": 1.0, "save_path": str(second_source.parent)},
        },
        torrent_files={
            "aaaa": [{"name": first_source.name, "size": 4}],
            "bbbb": [{"name": second_source.name, "size": 5}],
        },
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    # First request: completes normally, organizing to some filename.
    first = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(first.id, "downloading", result={"torrent_hash": "aaaa"})
    asyncio.run(worker._check_downloading())
    first_target = tmp_path / "library" / "Dune Part Two (2024) {tmdb-693134}" / first_source.name
    assert first_target.exists()

    # Second request: a different release (different filename), explicitly
    # an "overwrite" redownload.
    second = store.create_request(
        tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None, redownload_mode="overwrite"
    )
    store.update_status(second.id, "downloading", result={"torrent_hash": "bbbb"})

    asyncio.run(worker._check_downloading())

    second_target = tmp_path / "library" / "Dune Part Two (2024) {tmdb-693134}" / second_source.name
    assert second_target.exists()
    reloaded = store.get_request(second.id)
    assert reloaded.status == "complete"
    assert reloaded.result["superseded_paths"] == [str(first_target)]

    # And running the cleanup sweep actually removes the superseded file.
    asyncio.run(worker._attempt_source_cleanup(store.get_request(second.id)))
    assert not first_target.exists()
    assert second_target.exists()


def test_organize_and_complete_movie_does_not_supersede_on_plain_upgrade(tmp_path, monkeypatch):
    """redownload_mode="upgrade" (or None — an ordinary request) must
    never schedule anything for deletion, even if a prior organized copy
    exists for the same title — only an explicit "overwrite" does."""
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    store = RequestStore(":memory:")

    first_source = tmp_path / "downloads" / "Dune.Part.Two.2024.WEBRip.mkv"
    first_source.parent.mkdir(parents=True)
    first_source.write_bytes(b"data")
    second_source = tmp_path / "downloads" / "Dune.Part.Two.2024.BluRay.mkv"
    second_source.write_bytes(b"data2")
    qbt = FakeQBTClient(
        torrent_states={
            "aaaa": {"progress": 1.0, "save_path": str(first_source.parent)},
            "bbbb": {"progress": 1.0, "save_path": str(second_source.parent)},
        },
        torrent_files={
            "aaaa": [{"name": first_source.name, "size": 4}],
            "bbbb": [{"name": second_source.name, "size": 5}],
        },
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    first = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
    store.update_status(first.id, "downloading", result={"torrent_hash": "aaaa"})
    asyncio.run(worker._check_downloading())

    second = store.create_request(
        tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None, redownload_mode="upgrade"
    )
    store.update_status(second.id, "downloading", result={"torrent_hash": "bbbb"})

    asyncio.run(worker._check_downloading())

    reloaded = store.get_request(second.id)
    assert reloaded.status == "complete"
    assert not reloaded.result.get("superseded_paths")


def test_check_downloading_schedules_source_cleanup_after_organizing_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.mkv").write_bytes(b"ep1")
    (downloads / "Lanterns.S01E02.mkv").write_bytes(b"ep2")

    store = RequestStore(":memory:")
    show = store.create_show(tmdb_id=95350, title="Lanterns")
    row = store.create_pack_request(tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1)
    store.update_status(row.id, "downloading", result={"torrent_hash": "cccc"})
    qbt = FakeQBTClient(
        torrent_states={"cccc": {"progress": 1.0, "save_path": str(downloads)}},
        torrent_files={
            "cccc": [
                {"name": "Lanterns.S01E01.mkv", "size": 3},
                {"name": "Lanterns.S01E02.mkv", "size": 3},
            ]
        },
    )
    worker = Worker(store, FakeTMDBClient(), qbt)

    async def _run():
        await worker._check_downloading()
        await worker._run_due_source_cleanups()

    asyncio.run(_run())

    assert store.get_request(row.id).status == "complete"
    assert ("cccc", True) in qbt.deleted


def test_source_cleanup_survives_a_fresh_worker_instance(tmp_path, monkeypatch):
    """The actual regression this session's fix exists for: the original
    Stage 13.x cleanup lived only in an in-memory asyncio task, so a
    backend restart between organizing and the delay elapsing lost that
    work forever. Simulated here as literally as a unit test can — the
    Worker that organized the request is discarded (as if the process had
    restarted) and a brand new Worker, sharing only the same persistent
    store, is the one that runs the sweep."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
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
    first_worker = Worker(store, FakeTMDBClient(), qbt)
    asyncio.run(first_worker._check_downloading())
    assert store.get_request(row.id).status == "complete"
    assert store.get_request(row.id).source_cleanup_status == "pending"
    assert qbt.deleted == []  # not cleaned up yet — "first_worker" is about to be discarded, unswept

    del first_worker  # simulates the process restarting before the sweep ran
    second_worker = Worker(store, FakeTMDBClient(), qbt)
    asyncio.run(second_worker._run_due_source_cleanups())

    assert ("aaaa", True) in qbt.deleted
    assert store.get_request(row.id).source_cleanup_status == "done"


def test_source_cleanup_never_retries_once_marked_done(tmp_path):
    """Answers the user's explicit question directly: once cleanup is
    confirmed done, a later sweep must not touch that torrent again, even
    if source_cleanup_next_attempt_at is technically in the past."""
    target = tmp_path / "organized.mkv"
    target.write_bytes(b"data")
    store = RequestStore(":memory:")
    qbt = FakeQBTClient(torrent_states={"aaaa": {"progress": 1.0}})
    worker = Worker(store, FakeTMDBClient(), qbt)
    request_id = _pending_cleanup_row(store, "aaaa", [target], ["aaaa"])

    asyncio.run(worker._run_due_source_cleanups())
    assert len(qbt.deleted) == 1
    assert store.get_request(request_id).source_cleanup_status == "done"

    # A second sweep must find nothing due — list_due_source_cleanups only
    # ever returns 'pending' rows, and this one is now 'done'.
    asyncio.run(worker._run_due_source_cleanups())
    assert len(qbt.deleted) == 1  # unchanged — delete_torrent was not called again


# ---------------------------------------------------------------------------
# Stall recovery — a pick whose swarm never appeared (The Empty Man,
# live 2026-09-20). _check_downloading only ever recognised "gone" and
# "finished", so a torrent sitting at 0% with nobody to download from
# matched neither and stayed there indefinitely.
# ---------------------------------------------------------------------------


def _stalled_state(age_seconds: float) -> dict:
    return {"progress": 0, "num_seeds": 0, "added_on": time.time() - age_seconds}


def _downloading_row(store, torrent_hash: str, **result_extra):
    row = store.create_request(tmdb_id=693134, title="The Empty Man", release_year=2020, query=None)
    result = {
        "torrent_hash": torrent_hash,
        "winner": {"fileName": "The.Empty.Man.2020.2160p.DSNP.WEB-DL.x265-SiGLA"},
    }
    result.update(result_extra)
    store.update_status(row.id, "downloading", result=result)
    return row


def test_check_downloading_requeues_a_pick_whose_swarm_never_appeared():
    store = RequestStore(":memory:")
    row = _downloading_row(store, "dead")
    qbt = FakeQBTClient(torrent_states={"dead": _stalled_state(config.STALL_GRACE_SECONDS + 1)})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    refreshed = store.get_request(row.id)
    assert refreshed.status == "queued"
    assert ("dead", True) in qbt.deleted
    # Both blacklisted — the hash only rules out magnet listings, and the
    # name is what rules the release out when it came from a .torrent link.
    assert "dead" in store.get_rejected_torrent_hashes(693134)
    assert refreshed.result["stall_attempts"] == 2


def test_check_downloading_leaves_a_stalled_torrent_alone_inside_the_grace_period():
    """A torrent still finding peers looks identical to a dead one for
    the first few minutes, so the grace period is the whole safeguard."""
    store = RequestStore(":memory:")
    row = _downloading_row(store, "young")
    qbt = FakeQBTClient(torrent_states={"young": _stalled_state(60)})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "downloading"
    assert qbt.deleted == []


def test_check_downloading_leaves_a_slow_torrent_with_seeds_alone():
    """Zero progress is not enough on its own — a big torrent that has
    seeds but hasn't written a full percent yet must not be abandoned."""
    store = RequestStore(":memory:")
    row = _downloading_row(store, "slow")
    state = _stalled_state(config.STALL_GRACE_SECONDS + 1)
    state["num_seeds"] = 3
    qbt = FakeQBTClient(torrent_states={"slow": state})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    assert store.get_request(row.id).status == "downloading"
    assert qbt.deleted == []


def test_check_downloading_fails_the_request_once_attempts_are_exhausted():
    store = RequestStore(":memory:")
    row = _downloading_row(store, "dead-last", stall_attempts=config.STALL_MAX_ATTEMPTS)
    qbt = FakeQBTClient(torrent_states={"dead-last": _stalled_state(config.STALL_GRACE_SECONDS + 1)})
    worker = Worker(store, FakeTMDBClient(), qbt)

    asyncio.run(worker._check_downloading())

    refreshed = store.get_request(row.id)
    assert refreshed.status == "failed"
    assert "stalled with no seeds" in refreshed.error_message
    # Still blacklisted on the way out, so a manual retry can't re-pick it.
    assert "dead-last" in store.get_rejected_torrent_hashes(693134)


def test_start_runs_one_queue_consumer_per_search_slot():
    """Searching used to be strictly serial, so a queue of requests drained
    one at a time while an already-added download sat at the top of the
    list looking like the cause. SEARCH_CONCURRENCY consumers now share
    the queue."""
    store = RequestStore(":memory:")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    async def run():
        await worker.start()
        names = sorted(t.get_name() for t in worker._tasks if t.get_name().startswith("worker-queue"))
        await worker.stop()
        return names

    names = asyncio.run(run())

    assert len(names) == config.SEARCH_CONCURRENCY
    assert names == [f"worker-queue-{n}" for n in range(config.SEARCH_CONCURRENCY)]


def test_queued_requests_drain_together_rather_than_one_at_a_time():
    """Three requests queued at once are all searched concurrently. The
    fake's search blocks until every caller has arrived, so this can only
    pass if more than one consumer is running — it would deadlock to the
    timeout under the old single-lock design."""
    store = RequestStore(":memory:")
    rows = [
        store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)
        for _ in range(3)
    ]
    everyone_arrived = threading.Barrier(3, timeout=10)
    qbt = FakeQBTClient()
    counter = itertools.count()

    def gated_search(pattern, category="movies", plugins="enabled"):
        # Every caller waits here until all three have arrived, so this
        # can only return under real concurrency. Each gets its own
        # release, or the second and third would correctly fail as
        # duplicates of the first rather than proving anything.
        everyone_arrived.wait()
        n = next(counter)
        return [_result(fileUrl=f"magnet:?xt=urn:btih:AAA{n}")]

    qbt.search = gated_search
    worker = Worker(store, FakeTMDBClient(), qbt)

    async def run():
        await worker.start()
        for _ in range(100):
            if all(store.get_request(r.id).status not in ("queued", "searching") for r in rows):
                break
            await asyncio.sleep(0.05)
        await worker.stop()

    asyncio.run(run())

    # All three got past the barrier, so all three were searching at once.
    assert [store.get_request(r.id).status for r in rows] == ["downloading"] * 3


# ---------------------------------------------------------------------------
# _sweep_orphaned_downloads — the backstop for folders the per-request
# cleanup cannot reach, because it only ever deletes through qBittorrent
# and the torrent is routinely gone by then.
# ---------------------------------------------------------------------------


def _organized_download(tmp_path, monkeypatch, store, *, extra_video=None):
    """A finished download folder beside the library copy that was
    hardlinked out of it — the shape the sweep is meant to recognise."""
    library = tmp_path / "tv"
    downloads = library / "Some.Show.S01.1080p-GRP"
    (library / "Some Show {tmdb-1}" / "Season 01").mkdir(parents=True)
    downloads.mkdir(parents=True)
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library)
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "movies")

    source = downloads / "Some.Show.S01E01.1080p-GRP.mkv"
    source.write_bytes(b"episode one")
    filed = library / "Some Show {tmdb-1}" / "Season 01" / "Some Show - s01e01.mkv"
    os.link(source, filed)
    if extra_video:
        (downloads / extra_video).write_bytes(b"something nobody filed")

    row = store.create_request(tmdb_id=1, title="Some Show", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "h1", "winner": {"fileName": "x"}})
    store.mark_organized(row.id, [str(filed)], ["h1"], "2000-01-01T00:00:00+00:00")
    return downloads, filed


def test_orphan_sweep_removes_a_download_folder_already_filed(tmp_path, monkeypatch):
    """The case the per-request cleanup leaves behind: qBittorrent has
    no torrent left to delete through, so nothing ever collects this."""
    store = RequestStore(":memory:")
    downloads, filed = _organized_download(tmp_path, monkeypatch, store)
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._sweep_orphaned_downloads())

    assert not downloads.exists()
    assert filed.exists(), "the library copy must survive its download being removed"
    assert filed.read_bytes() == b"episode one"


def test_orphan_sweep_leaves_a_folder_holding_anything_unfiled(tmp_path, monkeypatch):
    """One unrecognised video and the whole folder stays — the non-video
    files beside it would go with it, so the bar is that nothing
    irreplaceable is in there."""
    store = RequestStore(":memory:")
    downloads, _ = _organized_download(tmp_path, monkeypatch, store, extra_video="Extra.Feature.mkv")
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._sweep_orphaned_downloads())

    assert downloads.exists()


def test_orphan_sweep_leaves_the_library_alone(tmp_path, monkeypatch):
    """The organized folder is itself under the library root, so the
    sweep walks past it every cycle; a filed path is never redundant."""
    store = RequestStore(":memory:")
    _, filed = _organized_download(tmp_path, monkeypatch, store)
    worker = Worker(store, FakeTMDBClient(), FakeQBTClient())

    asyncio.run(worker._sweep_orphaned_downloads())

    assert filed.exists()
    assert filed.parent.parent.exists()
