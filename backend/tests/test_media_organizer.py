import errno
import os

import pytest

from app import config
from app.media_organizer import (
    MediaOrganizerError,
    build_episode_path,
    organize_episode,
    select_video_file,
)
from app.tv_resolve import ShowIdentity

LANTERNS = ShowIdentity(
    tmdb_id=95350,
    title="Lanterns",
    original_title="Lanterns",
    variants=["Lanterns"],
    first_air_year=2026,
)

DISCOVERY = ShowIdentity(
    tmdb_id=67198,
    title="Star Trek: Discovery",
    original_title="Star Trek: Discovery",
    variants=["Star Trek: Discovery", "Star Trek"],
    first_air_year=None,
)


class FakeQBTClient:
    """Duck-typed stand-in — `select_video_file` only ever calls
    `torrent_info`/`torrent_files`, matching the fake-qbt pattern already
    used in test_pipeline.py."""

    def __init__(self, save_path, files, info=True):
        self._save_path = save_path
        self._files = files
        self._info = info

    def torrent_info(self, torrent_hash):
        if not self._info:
            return None
        return {"save_path": self._save_path}

    def torrent_files(self, torrent_hash):
        return self._files


# ---------------------------------------------------------------------------
# select_video_file
# ---------------------------------------------------------------------------


def test_select_video_file_picks_the_largest_video_file():
    qbt = FakeQBTClient(
        "/downloads/lanterns",
        [
            {"name": "Lanterns.S01E01.mkv", "size": 8_000_000_000},
            {"name": "Lanterns.S01E01.sample.mkv", "size": 50_000_000},
            {"name": "Lanterns.S01E01.nfo", "size": 1000},
        ],
    )
    result = select_video_file(qbt, "abc123")
    assert str(result) == "/downloads/lanterns/Lanterns.S01E01.mkv"


def test_select_video_file_skips_sample_named_file_even_if_larger():
    qbt = FakeQBTClient(
        "/downloads/lanterns",
        [
            {"name": "Lanterns.S01E01.mkv", "size": 8_000_000_000},
            {"name": "Lanterns.S01E01.Sample.mkv", "size": 9_000_000_000},
        ],
    )
    result = select_video_file(qbt, "abc123")
    assert result.name == "Lanterns.S01E01.mkv"


def test_select_video_file_ignores_non_video_extensions():
    qbt = FakeQBTClient(
        "/downloads/lanterns",
        [
            {"name": "Lanterns.S01E01.srt", "size": 999_999_999},  # deliberately huge, still not video
            {"name": "Lanterns.S01E01.mkv", "size": 1000},
        ],
    )
    result = select_video_file(qbt, "abc123")
    assert result.name == "Lanterns.S01E01.mkv"


def test_select_video_file_handles_subfolder_relative_names():
    qbt = FakeQBTClient(
        "/downloads",
        [{"name": "Lanterns S01E01/Lanterns.S01E01.mkv", "size": 8_000_000_000}],
    )
    result = select_video_file(qbt, "abc123")
    assert str(result) == "/downloads/Lanterns S01E01/Lanterns.S01E01.mkv"


def test_select_video_file_raises_when_torrent_not_found():
    qbt = FakeQBTClient("/downloads", [], info=False)
    with pytest.raises(MediaOrganizerError):
        select_video_file(qbt, "abc123")


def test_select_video_file_raises_when_nothing_qualifies():
    qbt = FakeQBTClient("/downloads", [{"name": "Lanterns.S01E01.nfo", "size": 1000}])
    with pytest.raises(MediaOrganizerError):
        select_video_file(qbt, "abc123")


# ---------------------------------------------------------------------------
# build_episode_path — folder/filename layout
# ---------------------------------------------------------------------------


def test_build_episode_path_includes_year_and_tmdb_hint():
    path = build_episode_path(LANTERNS, 1, 4, ".mkv")
    assert path == config.TV_LIBRARY_ROOT / "Lanterns (2026) {tmdb-95350}" / "Season 01" / "Lanterns - s01e04.mkv"


def test_build_episode_path_omits_year_when_unknown():
    path = build_episode_path(DISCOVERY, 1, 1, ".mkv")
    assert path.parent.parent.name == "Star Trek Discovery {tmdb-67198}"


def test_build_episode_path_zero_pads_season_and_episode():
    path = build_episode_path(LANTERNS, 2, 9, ".mp4")
    assert path.parent.name == "Season 02"
    assert path.name == "Lanterns - s02e09.mp4"


def test_build_episode_path_sanitizes_unsafe_characters_in_title():
    path = build_episode_path(DISCOVERY, 1, 1, ".mkv")
    assert ":" not in str(path)


def test_build_episode_path_supports_season_zero_for_specials():
    path = build_episode_path(LANTERNS, 0, 1, ".mkv")
    assert path.parent.name == "Season 00"


# ---------------------------------------------------------------------------
# organize_episode — real temp-filesystem hardlink/copy behavior
# ---------------------------------------------------------------------------


def test_organize_episode_hardlinks_into_place(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "downloads" / "Lanterns.S01E01.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"episode bytes")

    target = organize_episode(LANTERNS, 1, 1, source)

    assert target.read_bytes() == b"episode bytes"
    assert os.stat(source).st_ino == os.stat(target).st_ino  # same inode: a real hardlink, not a copy


def test_organize_episode_creates_missing_show_and_season_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"x")

    target = organize_episode(LANTERNS, 3, 7, source)

    assert target.parent.is_dir()
    assert target.parent.parent.name == "Lanterns (2026) {tmdb-95350}"


def test_organize_episode_is_idempotent_when_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"first")

    target = organize_episode(LANTERNS, 1, 1, source)
    source.write_bytes(b"second")  # simulate a re-run with an updated source file
    target = organize_episode(LANTERNS, 1, 1, source)

    assert target.read_bytes() == b"second"


def test_organize_episode_falls_back_to_copy_across_filesystems(tmp_path, monkeypatch):
    """os.link() raising EXDEV (cross-device) is the one real-world case a
    same-machine temp dir can't reproduce directly — simulate it instead of
    skipping this branch."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"episode bytes")

    def _raise_exdev(_src, _dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr("app.media_organizer.os.link", _raise_exdev)

    target = organize_episode(LANTERNS, 1, 1, source)

    assert target.read_bytes() == b"episode bytes"
    assert os.stat(source).st_ino != os.stat(target).st_ino  # a real copy, not a link


def test_organize_episode_falls_back_to_copy_when_filesystem_lacks_hardlink_support(tmp_path, monkeypatch):
    """Found live, not anticipated by the plan text: macOS's SMB client
    raises ENOTSUP (not EXDEV) when asked to hardlink across two paths on
    the same network share (real NAS TV-library validation, this session).
    Any filesystem that simply doesn't implement hardlinks needs the same
    copy fallback as a genuine cross-device case."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"episode bytes")

    def _raise_enotsup(_src, _dst):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr("app.media_organizer.os.link", _raise_enotsup)

    target = organize_episode(LANTERNS, 1, 1, source)

    assert target.read_bytes() == b"episode bytes"
    assert os.stat(source).st_ino != os.stat(target).st_ino
