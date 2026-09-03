from app.db import RequestStore


def _store() -> RequestStore:
    return RequestStore(":memory:")


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
