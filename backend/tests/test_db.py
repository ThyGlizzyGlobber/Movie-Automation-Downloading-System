from datetime import datetime, timedelta, timezone

from app.db import RequestStore


def _store() -> RequestStore:
    return RequestStore(":memory:")


def _backdate(store: RequestStore, request_id: int, days_ago: int) -> None:
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    store._conn.execute("UPDATE requests SET created_at = ? WHERE id = ?", (created_at, request_id))
    store._conn.commit()


def test_create_request_starts_queued():
    store = _store()
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query="dune part two")

    assert row.id == 1
    assert row.status == "queued"
    assert row.title == "Dune: Part Two"
    assert row.result is None
    assert row.created_at == row.updated_at


def test_get_request_missing_returns_none():
    store = _store()
    assert store.get_request(999) is None


def test_list_requests_newest_first():
    store = _store()
    first = store.create_request(tmdb_id=1, title="A", release_year=2020, query=None)
    second = store.create_request(tmdb_id=2, title="B", release_year=2021, query=None)

    rows = store.list_requests()

    assert [r.id for r in rows] == [second.id, first.id]


def test_list_requests_filters_by_status():
    store = _store()
    store.create_request(tmdb_id=1, title="A", release_year=2020, query=None)
    second = store.create_request(tmdb_id=2, title="B", release_year=2021, query=None)
    store.update_status(second.id, "downloading")

    rows = store.list_requests(status="downloading")

    assert [r.id for r in rows] == [second.id]


def test_update_status_persists_result_and_error():
    store = _store()
    row = store.create_request(tmdb_id=1, title="A", release_year=2020, query=None)

    store.update_status(row.id, "failed", error_message="boom")
    reloaded = store.get_request(row.id)
    assert reloaded.status == "failed"
    assert reloaded.error_message == "boom"

    store.update_status(row.id, "downloading", result={"torrent_hash": "abcd"})
    reloaded = store.get_request(row.id)
    assert reloaded.status == "downloading"
    assert reloaded.result == {"torrent_hash": "abcd"}


def test_update_status_without_result_keeps_previous_result():
    store = _store()
    row = store.create_request(tmdb_id=1, title="A", release_year=2020, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "abcd"})

    store.update_status(row.id, "complete")

    assert store.get_request(row.id).result == {"torrent_hash": "abcd"}


def test_queued_request_ids_only_returns_queued():
    store = _store()
    queued = store.create_request(tmdb_id=1, title="A", release_year=2020, query=None)
    other = store.create_request(tmdb_id=2, title="B", release_year=2021, query=None)
    store.update_status(other.id, "failed")

    assert store.queued_request_ids() == [queued.id]


def test_recover_interrupted_fails_only_searching_rows():
    store = _store()
    searching = store.create_request(tmdb_id=1, title="A", release_year=2020, query=None)
    store.update_status(searching.id, "searching")
    downloading = store.create_request(tmdb_id=2, title="B", release_year=2021, query=None)
    store.update_status(downloading.id, "downloading", result={"torrent_hash": "abcd"})
    queued = store.create_request(tmdb_id=3, title="C", release_year=2022, query=None)

    recovered = store.recover_interrupted()

    assert recovered == 1
    assert store.get_request(searching.id).status == "failed"
    assert store.get_request(searching.id).error_message == "interrupted, please retry"
    assert store.get_request(downloading.id).status == "downloading"
    assert store.get_request(queued.id).status == "queued"


def test_settings_table_seeded_with_single_row():
    store = _store()
    row = store._conn.execute("SELECT * FROM settings").fetchone()
    assert row["id"] == 1
    assert row["data_json"] == "{}"


def test_get_settings_starts_empty():
    store = _store()
    assert store.get_settings() == {}


def test_update_settings_merges_without_clobbering_other_keys():
    store = _store()
    store.update_settings({"plex_token": "abc"})

    merged = store.update_settings({"request_retention_days": 90})

    assert merged == {"plex_token": "abc", "request_retention_days": 90}
    assert store.get_settings() == {"plex_token": "abc", "request_retention_days": 90}


def test_update_settings_stores_none_rather_than_removing_key():
    store = _store()
    store.update_settings({"plex_token": "abc"})

    store.update_settings({"plex_token": None})

    assert store.get_settings() == {"plex_token": None}


def test_purge_requests_older_than_deletes_only_old_terminal_rows():
    store = _store()
    old_complete = store.create_request(tmdb_id=1, title="Old Complete", release_year=2020, query=None)
    store.update_status(old_complete.id, "complete")
    _backdate(store, old_complete.id, days_ago=100)

    old_downloading = store.create_request(tmdb_id=2, title="Old Downloading", release_year=2020, query=None)
    store.update_status(old_downloading.id, "downloading", result={"torrent_hash": "abcd"})
    _backdate(store, old_downloading.id, days_ago=100)

    recent_complete = store.create_request(tmdb_id=3, title="Recent Complete", release_year=2020, query=None)
    store.update_status(recent_complete.id, "complete")

    removed = store.purge_requests_older_than(days=90)

    assert removed == 1
    remaining_ids = {r.id for r in store.list_requests()}
    assert remaining_ids == {old_downloading.id, recent_complete.id}


def test_create_request_defaults_to_movie_media_type():
    store = _store()
    row = store.create_request(tmdb_id=693134, title="Dune: Part Two", release_year=2024, query=None)

    assert row.media_type == "movie"
    assert row.show_id is None
    assert row.season_number is None
    assert row.episode_number is None


# ---------------------------------------------------------------------------
# Stage 12 — shows (standing subscriptions) & show_episodes (dedup ledger)
# ---------------------------------------------------------------------------


def test_create_episode_request_sets_episode_fields():
    store = _store()
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    row = store.create_episode_request(
        tmdb_id=95350, show_id=show.id, title="Lanterns", season_number=1, episode_number=4
    )

    assert row.media_type == "episode"
    assert row.show_id == show.id
    assert row.season_number == 1
    assert row.episode_number == 4
    assert row.status == "queued"
    assert row.query is None
    assert row.release_year is None


def test_create_show_starts_watching_with_no_last_checked():
    store = _store()
    row = store.create_show(tmdb_id=95350, title="Lanterns")

    assert row.id == 1
    assert row.status == "watching"
    assert row.last_checked_at is None


def test_get_show_by_tmdb_id_missing_returns_none():
    store = _store()
    assert store.get_show_by_tmdb_id(999) is None


def test_get_show_by_tmdb_id_finds_it():
    store = _store()
    show = store.create_show(tmdb_id=95350, title="Lanterns")

    assert store.get_show_by_tmdb_id(95350).id == show.id


def test_list_shows_filters_by_status():
    store = _store()
    watching = store.create_show(tmdb_id=1, title="A")
    paused = store.create_show(tmdb_id=2, title="B")
    store.update_show_status(paused.id, "paused")

    assert [s.id for s in store.list_shows(status="watching")] == [watching.id]
    assert [s.id for s in store.list_shows(status="paused")] == [paused.id]


def test_update_show_status_pauses_and_resumes():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")

    store.update_show_status(show.id, "paused")
    assert store.get_show(show.id).status == "paused"

    store.update_show_status(show.id, "watching")
    assert store.get_show(show.id).status == "watching"


def test_update_show_last_checked_sets_timestamp():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")

    store.update_show_last_checked(show.id)

    assert store.get_show(show.id).last_checked_at is not None


def test_delete_show_removes_it_but_keeps_history():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")
    request_row = store.create_episode_request(tmdb_id=1, show_id=show.id, title="A", season_number=1, episode_number=1)
    store.add_show_episode(show.id, 1, 1, request_row.id)

    assert store.delete_show(show.id) is True

    assert store.get_show(show.id) is None
    assert store.get_request(request_row.id) is not None
    assert store.has_show_episode(show.id, 1, 1) is True  # ledger row untouched


def test_delete_show_missing_returns_false():
    store = _store()
    assert store.delete_show(999) is False


def test_has_show_episode_false_until_added():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")

    assert store.has_show_episode(show.id, 1, 1) is False

    store.add_show_episode(show.id, 1, 1, request_id=1)

    assert store.has_show_episode(show.id, 1, 1) is True


def test_new_show_episode_starts_with_zero_recheck_count():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")
    store.add_show_episode(show.id, 1, 1, request_id=1)

    [row] = store.list_show_episodes(show.id)
    assert row.recheck_count == 0
    assert row.last_rechecked_at is None


def test_list_show_episodes_filters_by_show():
    store = _store()
    show_a = store.create_show(tmdb_id=1, title="A")
    show_b = store.create_show(tmdb_id=2, title="B")
    store.add_show_episode(show_a.id, 1, 1, request_id=1)
    store.add_show_episode(show_b.id, 1, 1, request_id=2)

    rows = store.list_show_episodes(show_a.id)

    assert len(rows) == 1
    assert rows[0].show_id == show_a.id


def test_list_show_episodes_without_show_id_returns_all():
    store = _store()
    show_a = store.create_show(tmdb_id=1, title="A")
    show_b = store.create_show(tmdb_id=2, title="B")
    store.add_show_episode(show_a.id, 1, 1, request_id=1)
    store.add_show_episode(show_b.id, 1, 1, request_id=2)

    assert len(store.list_show_episodes()) == 2


def test_record_episode_recheck_bumps_count_and_timestamp_without_new_request():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")
    store.add_show_episode(show.id, 1, 1, request_id=1)
    [row] = store.list_show_episodes(show.id)

    store.record_episode_recheck(row.id)

    [reloaded] = store.list_show_episodes(show.id)
    assert reloaded.recheck_count == 1
    assert reloaded.last_rechecked_at is not None
    assert reloaded.request_id == 1  # unchanged — nothing new was found


def test_record_episode_recheck_repoints_request_id_when_given_one():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")
    store.add_show_episode(show.id, 1, 1, request_id=1)
    [row] = store.list_show_episodes(show.id)

    store.record_episode_recheck(row.id, new_request_id=99)

    [reloaded] = store.list_show_episodes(show.id)
    assert reloaded.recheck_count == 1
    assert reloaded.request_id == 99


def test_record_episode_recheck_accumulates_across_multiple_calls():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")
    store.add_show_episode(show.id, 1, 1, request_id=1)
    [row] = store.list_show_episodes(show.id)

    store.record_episode_recheck(row.id)
    store.record_episode_recheck(row.id)

    [reloaded] = store.list_show_episodes(show.id)
    assert reloaded.recheck_count == 2


def test_add_show_episode_is_idempotent_via_unique_constraint():
    store = _store()
    show = store.create_show(tmdb_id=1, title="A")

    store.add_show_episode(show.id, 1, 1, request_id=1)
    store.add_show_episode(show.id, 1, 1, request_id=2)  # INSERT OR IGNORE, not a crash

    rows = store._conn.execute(
        "SELECT * FROM show_episodes WHERE show_id = ? AND season_number = 1 AND episode_number = 1", (show.id,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["request_id"] == 1  # first insert wins


def test_purge_requests_older_than_zero_clears_all_terminal_regardless_of_age():
    store = _store()
    recent_failed = store.create_request(tmdb_id=1, title="Recent Failed", release_year=2020, query=None)
    store.update_status(recent_failed.id, "failed", error_message="boom")
    active = store.create_request(tmdb_id=2, title="Active", release_year=2020, query=None)

    removed = store.purge_requests_older_than(days=0)

    assert removed == 1
    assert {r.id for r in store.list_requests()} == {active.id}
