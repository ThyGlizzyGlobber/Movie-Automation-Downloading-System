"""app/drives.py: a box's separate drives, and the download and filing steps
that use them. Off a box there's one implicit drive and nothing changes."""

import asyncio
import json
from pathlib import Path

import pytest

from app import config, drives, pipeline, worker
from app.media_organizer import organize_movie
from app.resolve import MediaIdentity


def add_drive(media: Path, name: str, label: str | None = None, size: int = 8_000_000_000_000) -> Path:
    """A drive the way the NAS updater leaves one."""
    folder = media / name
    for sub in ("downloads/movies", "downloads/tv", "Movies", "TV Shows"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    (folder / drives.MARKER).write_text(json.dumps({"label": label or name.replace("drive", "Drive "), "size_bytes": size}))
    return folder


@pytest.fixture
def box(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", str(media))
    return media


# -- off a box --


def test_off_a_box_there_is_one_drive_the_configured_library(tmp_path, monkeypatch):
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", None)
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "movies")
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "tv")
    (only,) = drives.all_drives()
    assert (only.movies, only.tv, only.downloads) == (tmp_path / "movies", tmp_path / "tv", None)
    assert drives.library_root("movie", near="/anywhere/at/all.mkv") == tmp_path / "movies"
    assert drives.download_roots() == [tmp_path / "movies", tmp_path / "tv"]
    assert drives.ready()


def test_off_a_box_downloads_go_where_qbittorrent_says(monkeypatch):
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", None)

    class QBT:
        def free_space_bytes(self):
            return 500

    assert pipeline.download_target(QBT(), None, "movies") == (500, None)


# -- on a box --


def test_a_box_s_drives_are_the_folders_the_updater_marked_in_number_order(box):
    add_drive(box, "drive10")
    add_drive(box, "drive2", "Drive 2")
    add_drive(box, "drive1")
    (box / "half-made").mkdir()  # no marker: not a drive
    found = drives.all_drives()
    assert [d.id for d in found] == ["drive1", "drive2", "drive10"]
    assert found[1].label == "Drive 2" and found[1].size_bytes == 8_000_000_000_000
    assert found[0].movies == box / "drive1" / "Movies" and found[0].tv == box / "drive1" / "TV Shows"
    assert drives.library_roots("tv") == [box / d / "TV Shows" for d in ("drive1", "drive2", "drive10")]


def test_a_box_without_a_drive_has_nowhere_to_download(box):
    assert drives.all_drives() == [] and not drives.ready()
    assert drives.download_drive() is None
    with pytest.raises(drives.NoDriveError, match="Settings › Storage"):
        pipeline.download_target(object(), None, "movies")


def test_a_download_goes_to_the_drive_with_the_most_space(box, monkeypatch):
    add_drive(box, "drive1")
    add_drive(box, "drive2")
    space = {"drive1": 100 * 1024**3, "drive2": 900 * 1024**3}
    monkeypatch.setattr(drives, "free_bytes", lambda d: space[d.id])

    class Settings:
        free_space_floor_gb = 50

    free, save_path = pipeline.download_target(object(), Settings(), "movies")
    assert save_path == str(box / "drive2" / "downloads" / "movies")
    assert free == (900 - 50) * 1024**3  # the floor applies to the chosen drive


def test_qbittorrent_is_told_which_drive_to_save_on(box):
    added = []

    class QBT:
        def ensure_category(self, category):
            pass

        def existing_torrent_hashes(self):
            return {"new"} if added else set()

        def add_torrent(self, url, category, save_path=None):
            added.append((url, category, save_path))

        def torrent_info(self, h):
            return {"name": "Heat 1995 1080p"}

        def list_torrents(self):
            return [{"hash": "new", "name": "Heat 1995 1080p"}]

    candidate = {"fileUrl": "magnet:?xt=urn:btih:AAAA", "fileName": "Heat 1995 1080p BluRay x264", "fileSize": 8 * 1024**3,
                 "nbSeeders": 50, "_variant_used": "Heat"}
    pipeline._rank_and_add(QBT(), [candidate], 0, "movies", set(), "/media/drive2/downloads/movies")
    assert added and added[0][2] == "/media/drive2/downloads/movies"


def test_a_download_is_filed_on_its_own_drive_as_a_hardlink(box):
    add_drive(box, "drive1")
    add_drive(box, "drive2")
    source = box / "drive2" / "downloads" / "movies" / "Heat.1995.1080p" / "Heat.1995.1080p.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not really a movie")
    identity = MediaIdentity(tmdb_id=949, title="Heat", original_title="Heat", release_year=1995, variants=["Heat"])

    target = organize_movie(identity, source)

    assert target == box / "drive2" / "Movies" / "Heat (1995) {tmdb-949}" / "Heat.1995.1080p.mkv"
    assert target.stat().st_ino == source.stat().st_ino


def test_something_on_no_drive_is_filed_on_the_first(box):
    add_drive(box, "drive1")
    add_drive(box, "drive2")
    assert drives.library_root("tv", near="/somewhere/else.mkv") == box / "drive1" / "TV Shows"


def test_the_sweeps_look_in_every_drive_s_downloads(box):
    add_drive(box, "drive1")
    add_drive(box, "drive2")
    assert drives.download_roots() == [
        box / "drive1" / "downloads" / "movies", box / "drive1" / "downloads" / "tv",
        box / "drive2" / "downloads" / "movies", box / "drive2" / "downloads" / "tv",
    ]


def test_a_request_waits_for_a_drive_rather_than_failing(box, monkeypatch):
    monkeypatch.setattr(worker, "NO_DRIVE_RECHECK_SECONDS", 0)
    ran = []
    checks = iter([False, False, True])
    monkeypatch.setattr(drives, "ready", lambda: next(checks))

    class W:
        queue = asyncio.Queue()
        _pending = {7}
        _search_slots = asyncio.Semaphore(1)

        async def _run_one(self, request_id):
            ran.append(request_id)
            raise asyncio.CancelledError  # stop the consumer after one

    async def go():
        w = W()
        w.queue.put_nowait(7)
        with pytest.raises(asyncio.CancelledError):
            await worker.Worker._process_queue(w)

    asyncio.run(go())
    assert ran == [7]
