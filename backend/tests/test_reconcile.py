import os
from pathlib import Path

from app.db import RequestStore
from app.reconcile import (
    find_orphaned_download_dirs,
    find_redundant_sources,
    remove_orphaned_download_dirs,
    remove_redundant_sources,
)


class FakeQBT:
    def __init__(self, torrents, fail_on=None):
        self._torrents = torrents
        self.fail_on = fail_on
        self.deleted: list[tuple[str, bool]] = []

    def list_torrents(self):
        return list(self._torrents)

    def delete_torrent(self, torrent_hash, delete_files=True):
        if self.fail_on == torrent_hash:
            raise RuntimeError("qbittorrent unreachable")
        self.deleted.append((torrent_hash, delete_files))


def _filed(store, tmdb_id, torrent_hash, path, media_type="movie"):
    """A completed, organized request — the ledger row is what the
    reconciler actually reads, so it has to come from the real path
    that writes one."""
    row = store.create_request(tmdb_id=tmdb_id, title=f"T{tmdb_id}", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": torrent_hash, "winner": {"fileName": "x"}})
    store.mark_organized(row.id, [str(path)], [torrent_hash], "2000-01-01T00:00:00+00:00")
    return row


def _torrent(torrent_hash, name="Some.Release.2024", progress=1.0, size=1024**3):
    return {"hash": torrent_hash, "name": name, "progress": progress, "size": size}


def test_finds_a_torrent_whose_filed_copy_is_still_in_place(tmp_path):
    filed_path = tmp_path / "Movie (2024).mkv"
    filed_path.write_bytes(b"x")
    store = RequestStore(":memory:")
    _filed(store, 1, "aaaa", filed_path)
    qbt = FakeQBT([_torrent("aaaa")])

    found = find_redundant_sources(store, qbt)

    assert [f["hash"] for f in found] == ["aaaa"]
    assert found[0]["filed_paths"] == [str(filed_path)]


def test_leaves_a_torrent_this_app_never_filed(tmp_path):
    """Someone's own torrent has no ledger row, and must never be
    touched — the reconciler only ever removes what this app itself
    filed into the library."""
    store = RequestStore(":memory:")
    qbt = FakeQBT([_torrent("stranger")])

    assert find_redundant_sources(store, qbt) == []


def test_leaves_a_torrent_whose_filed_copy_has_gone(tmp_path):
    """The same all-or-nothing rule the sweep uses: while a filed copy is
    missing the original may be the only copy left."""
    store = RequestStore(":memory:")
    _filed(store, 1, "aaaa", tmp_path / "never-written.mkv")
    qbt = FakeQBT([_torrent("aaaa")])

    assert find_redundant_sources(store, qbt) == []


def test_leaves_a_torrent_that_is_still_downloading(tmp_path):
    """A hash can be in the ledger from an earlier file while a re-add of
    the same torrent is mid-flight; either way, never delete something
    still transferring."""
    filed_path = tmp_path / "Movie (2024).mkv"
    filed_path.write_bytes(b"x")
    store = RequestStore(":memory:")
    _filed(store, 1, "aaaa", filed_path)
    qbt = FakeQBT([_torrent("aaaa", progress=0.4)])

    assert find_redundant_sources(store, qbt) == []


def test_all_filed_paths_must_be_present_not_just_one(tmp_path):
    """A season pack files many episodes under one hash. One missing is
    enough to leave the whole torrent alone."""
    present = tmp_path / "S01E01.mkv"
    present.write_bytes(b"x")
    store = RequestStore(":memory:")
    row = store.create_request(tmdb_id=7, title="Show", release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "pack", "winner": {"fileName": "x"}})
    store.mark_organized(
        row.id, [str(present), str(tmp_path / "S01E02.mkv")], ["pack"], "2000-01-01T00:00:00+00:00"
    )
    qbt = FakeQBT([_torrent("pack")])

    assert find_redundant_sources(store, qbt) == []


def test_dry_run_reports_without_deleting(tmp_path):
    filed_path = tmp_path / "Movie (2024).mkv"
    filed_path.write_bytes(b"x")
    store = RequestStore(":memory:")
    _filed(store, 1, "aaaa", filed_path)
    qbt = FakeQBT([_torrent("aaaa")])

    found = remove_redundant_sources(store, qbt, apply=False)

    assert [f["removed"] for f in found] == [False]
    assert qbt.deleted == []


def test_apply_deletes_the_torrent_and_its_files(tmp_path):
    filed_path = tmp_path / "Movie (2024).mkv"
    filed_path.write_bytes(b"x")
    store = RequestStore(":memory:")
    _filed(store, 1, "aaaa", filed_path)
    qbt = FakeQBT([_torrent("aaaa")])

    found = remove_redundant_sources(store, qbt, apply=True)

    assert [f["removed"] for f in found] == [True]
    assert qbt.deleted == [("aaaa", True)]
    # The library copy is never what gets deleted.
    assert filed_path.exists()


def test_one_failure_does_not_stop_the_rest(tmp_path):
    """A partial run reports honestly rather than raising halfway and
    leaving the caller guessing which ones went."""
    first, second = tmp_path / "a.mkv", tmp_path / "b.mkv"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    store = RequestStore(":memory:")
    _filed(store, 1, "aaaa", first)
    _filed(store, 2, "bbbb", second)
    qbt = FakeQBT([_torrent("aaaa"), _torrent("bbbb")], fail_on="aaaa")

    found = remove_redundant_sources(store, qbt, apply=True)

    by_hash = {f["hash"]: f for f in found}
    assert by_hash["aaaa"]["removed"] is False
    assert "unreachable" in by_hash["aaaa"]["error"]
    assert by_hash["bbbb"]["removed"] is True
    assert qbt.deleted == [("bbbb", True)]


def test_ledger_survives_history_being_cleared(tmp_path):
    """The whole point: the request row carrying pending_cleanup_hashes
    is gone, which is how these folders were stranded, but the ledger
    still knows the torrent was filed."""
    filed_path = tmp_path / "Movie (2024).mkv"
    filed_path.write_bytes(b"x")
    store = RequestStore(":memory:")
    row = _filed(store, 1, "aaaa", filed_path)
    store.mark_source_cleanup_done(row.id)
    store.purge_requests_older_than(days=-1)
    assert store.get_request(row.id) is None

    found = find_redundant_sources(store, FakeQBT([_torrent("aaaa")]))

    assert [f["hash"] for f in found] == ["aaaa"]


# ---------------------------------------------------------------------------
# The filesystem side: folders qBittorrent no longer has a torrent for.
# ---------------------------------------------------------------------------


def _library_file(store, library: Path, name: str, data: bytes = b"movie-bytes") -> Path:
    """A filed copy, recorded in the ledger the way organizing does."""
    library.mkdir(parents=True, exist_ok=True)
    filed = library / name
    filed.write_bytes(data)
    row = store.create_request(tmdb_id=abs(hash(name)) % 10000, title=name, release_year=2024, query=None)
    store.update_status(row.id, "downloading", result={"torrent_hash": "x" * 8, "winner": {"fileName": name}})
    store.mark_organized(row.id, [str(filed)], [], "2000-01-01T00:00:00+00:00")
    return filed


def test_finds_a_download_folder_hardlinked_into_the_library(tmp_path):
    """The case qBittorrent can't help with: it removed the torrent on
    completion, so nothing links the folder back to a request. The
    hardlink does."""
    library, downloads = tmp_path / "library", tmp_path / "downloads"
    store = RequestStore(":memory:")
    filed = _library_file(store, library, "Movie (2024).mkv")
    leftover = downloads / "Movie.2024.1080p-GRP"
    leftover.mkdir(parents=True)
    os.link(filed, leftover / "Movie.2024.1080p-GRP.mkv")
    (leftover / "release.nfo").write_bytes(b"junk")

    found = find_orphaned_download_dirs(store, [downloads])

    assert [f["path"] for f in found] == [str(leftover)]
    assert found[0]["filed_as"] == [str(filed)]


def test_leaves_a_folder_holding_a_video_that_is_not_in_the_library(tmp_path):
    """One unrecognised video and the whole folder stays — it may be the
    only copy of something."""
    library, downloads = tmp_path / "library", tmp_path / "downloads"
    store = RequestStore(":memory:")
    filed = _library_file(store, library, "Movie (2024).mkv")
    leftover = downloads / "Pack"
    leftover.mkdir(parents=True)
    os.link(filed, leftover / "filed.mkv")
    (leftover / "never-filed.mkv").write_bytes(b"unique")

    assert find_orphaned_download_dirs(store, [downloads]) == []


def test_leaves_a_copy_that_only_resembles_a_filed_file(tmp_path):
    """Identical name and bytes, but its own inode — it is not the same
    data, so it is not provably redundant and is never touched."""
    library, downloads = tmp_path / "library", tmp_path / "downloads"
    store = RequestStore(":memory:")
    _library_file(store, library, "Movie (2024).mkv", b"movie-bytes")
    leftover = downloads / "Movie.2024"
    leftover.mkdir(parents=True)
    (leftover / "Movie (2024).mkv").write_bytes(b"movie-bytes")

    assert find_orphaned_download_dirs(store, [downloads]) == []


def test_never_removes_the_library_folder_itself(tmp_path):
    """Scanning a root that also holds organized files must not offer to
    delete them — the filed path is recognised as the keeper, not a
    duplicate."""
    library = tmp_path / "library"
    store = RequestStore(":memory:")
    movie_dir = library / "Movie (2024) {tmdb-1}"
    _library_file(store, movie_dir, "Movie (2024).mkv")

    assert find_orphaned_download_dirs(store, [library]) == []


def test_apply_removes_the_folder_and_leaves_the_library_copy(tmp_path):
    library, downloads = tmp_path / "library", tmp_path / "downloads"
    store = RequestStore(":memory:")
    filed = _library_file(store, library, "Movie (2024).mkv")
    leftover = downloads / "Movie.2024.1080p-GRP"
    leftover.mkdir(parents=True)
    os.link(filed, leftover / "Movie.2024.1080p-GRP.mkv")

    found = remove_orphaned_download_dirs(store, [downloads], apply=True)

    assert [f["removed"] for f in found] == [True]
    assert not leftover.exists()
    assert filed.exists() and filed.read_bytes() == b"movie-bytes"


def test_dry_run_removes_nothing(tmp_path):
    library, downloads = tmp_path / "library", tmp_path / "downloads"
    store = RequestStore(":memory:")
    filed = _library_file(store, library, "Movie (2024).mkv")
    leftover = downloads / "Movie.2024"
    leftover.mkdir(parents=True)
    os.link(filed, leftover / "Movie.2024.mkv")

    found = remove_orphaned_download_dirs(store, [downloads], apply=False)

    assert [f["removed"] for f in found] == [False]
    assert leftover.exists()


def test_a_missing_root_is_skipped_not_fatal(tmp_path):
    store = RequestStore(":memory:")
    _library_file(store, tmp_path / "library", "Movie (2024).mkv")

    assert find_orphaned_download_dirs(store, [tmp_path / "nope"]) == []


def test_cli_dry_run_reports_both_kinds_and_deletes_nothing(tmp_path, monkeypatch, capsys):
    """Wiring check for `python -m app.cli cleanup-orphans`: both halves
    reported in one run, and nothing touched without --apply."""
    from app import cli

    library, downloads = tmp_path / "library", tmp_path / "downloads"
    store = RequestStore(":memory:")
    filed = _library_file(store, library, "Movie (2024).mkv")
    leftover = downloads / "Movie.2024.1080p-GRP"
    leftover.mkdir(parents=True)
    os.link(filed, leftover / "Movie.2024.1080p-GRP.mkv")

    tracked = tmp_path / "library" / "Show S01E01.mkv"
    tracked.write_bytes(b"ep")
    _filed(store, 42, "aaaa", tracked)

    monkeypatch.setattr(cli, "RequestStore", lambda _path: store)
    monkeypatch.setattr(cli, "QBTClient", lambda *a, **k: FakeQBT([_torrent("aaaa")]))

    exit_code = cli.main(["cleanup-orphans", "--root", str(downloads)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "would remove" in out
    assert "torrent  aaaa" in out
    assert str(leftover) in out
    assert "Re-run with --apply" in out
    assert leftover.exists()  # dry run really is read-only
