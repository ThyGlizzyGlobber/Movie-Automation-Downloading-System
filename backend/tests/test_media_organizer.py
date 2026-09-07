import errno
import os
from pathlib import Path

import pytest

from app import config
from app.media_organizer import (
    MediaOrganizerError,
    build_episode_path,
    build_movie_path,
    find_existing_episode_file,
    organize_episode,
    organize_movie,
    organize_pack,
    select_video_file,
    translate_qbit_save_path,
)
from app.resolve import MediaIdentity
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

DUNE = MediaIdentity(
    tmdb_id=693134,
    title="Dune: Part Two",
    original_title="Dune: Part Two",
    release_year=2024,
    variants=["Dune: Part Two"],
)

UNTITLED = MediaIdentity(
    tmdb_id=1,
    title="Untitled Project",
    original_title="Untitled Project",
    release_year=None,
    variants=["Untitled Project"],
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


def test_select_video_file_translates_qbit_container_path_when_configured():
    """The real bug: qBittorrent and this backend run as separate
    containers, each with their own bind mount of the identical host
    directory under a different internal path. qBittorrent's own
    `save_path` ("/media/TV Shows/...") means nothing inside this
    container unless translated into this container's own mount
    ("/tv-library/...")."""
    qbt = FakeQBTClient(
        "/media/TV Shows/Lanterns S01E01",
        [{"name": "Lanterns.S01E01.mkv", "size": 8_000_000_000}],
    )
    result = select_video_file(qbt, "abc123", "/media/TV Shows", Path("/tv-library"))
    assert str(result) == "/tv-library/Lanterns S01E01/Lanterns.S01E01.mkv"


def test_select_video_file_leaves_save_path_untouched_when_qbit_root_unset():
    """No QBIT_TV_SAVE_PATH configured -> no translation, same as every
    environment that doesn't run two containers against one shared mount
    (tests, this workstation, a single-container deployment)."""
    qbt = FakeQBTClient(
        "/downloads/lanterns",
        [{"name": "Lanterns.S01E01.mkv", "size": 8_000_000_000}],
    )
    result = select_video_file(qbt, "abc123", None, Path("/tv-library"))
    assert str(result) == "/downloads/lanterns/Lanterns.S01E01.mkv"


def test_select_video_file_leaves_save_path_untouched_when_it_does_not_match_qbit_root():
    """An unexpected save_path (doesn't start with the configured
    qbit_root) is left alone rather than silently mangled into a wrong
    local path — fail safe, not best guess."""
    qbt = FakeQBTClient(
        "/some/other/path/lanterns",
        [{"name": "Lanterns.S01E01.mkv", "size": 8_000_000_000}],
    )
    result = select_video_file(qbt, "abc123", "/media/TV Shows", Path("/tv-library"))
    assert str(result) == "/some/other/path/lanterns/Lanterns.S01E01.mkv"


# ---------------------------------------------------------------------------
# translate_qbit_save_path
# ---------------------------------------------------------------------------


def test_translate_qbit_save_path_rewrites_matching_prefix():
    result = translate_qbit_save_path("/media/TV Shows/Lanterns S01", "/media/TV Shows", Path("/tv-library"))
    assert result == Path("/tv-library/Lanterns S01")


def test_translate_qbit_save_path_rewrites_exact_root():
    result = translate_qbit_save_path("/media/TV Shows", "/media/TV Shows", Path("/tv-library"))
    assert result == Path("/tv-library")


def test_translate_qbit_save_path_returns_unmodified_when_root_unset():
    result = translate_qbit_save_path("/media/TV Shows/Lanterns S01", None, Path("/tv-library"))
    assert result == Path("/media/TV Shows/Lanterns S01")


def test_translate_qbit_save_path_returns_unmodified_when_prefix_does_not_match():
    result = translate_qbit_save_path("/media/Movies/Dune", "/media/TV Shows", Path("/tv-library"))
    assert result == Path("/media/Movies/Dune")


# ---------------------------------------------------------------------------
# find_existing_episode_file — Stage 12's pre-subscribe "already on disk"
# check, so a first-time subscribe doesn't re-grab what's already there
# ---------------------------------------------------------------------------


def test_find_existing_episode_file_matches_the_apps_own_organized_naming(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    target = tmp_path / "Lanterns (2026) {tmdb-95350}" / "Season 01" / "Lanterns - s01e01.mkv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"data")

    found = find_existing_episode_file(LANTERNS, 1, 1)

    assert found == target


def test_find_existing_episode_file_matches_a_raw_unorganized_release_name(tmp_path, monkeypatch):
    """A torrent this app added but never successfully organized (e.g. the
    max_ratio race flagged in project.md's Stage 11 decision log), or one
    added completely outside this app, still sits under its own scene-
    release name rather than this app's `- sNNeNN` convention — must still
    be found."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    target = tmp_path / "Lanterns.S01E02.2160p.AMZN.WEB-DL.DDP5.1.DV.HDR.H.265-G66.mkv"
    target.write_bytes(b"data")

    found = find_existing_episode_file(LANTERNS, 1, 2)

    assert found == target


def test_find_existing_episode_file_returns_none_when_nothing_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    (tmp_path / "Some Other Show S01E01.mkv").write_bytes(b"data")

    assert find_existing_episode_file(LANTERNS, 1, 1) is None


def test_find_existing_episode_file_does_not_match_a_different_episode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    (tmp_path / "Lanterns.S01E02.2160p.mkv").write_bytes(b"data")

    assert find_existing_episode_file(LANTERNS, 1, 1) is None


def test_find_existing_episode_file_ignores_non_video_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    (tmp_path / "Lanterns.S01E01.nfo").write_bytes(b"data")
    (tmp_path / "Lanterns.S01E01.srt").write_bytes(b"data")

    assert find_existing_episode_file(LANTERNS, 1, 1) is None


def test_find_existing_episode_file_returns_none_when_root_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "does-not-exist")

    assert find_existing_episode_file(LANTERNS, 1, 1) is None


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


# ---------------------------------------------------------------------------
# build_movie_path / organize_movie — folder renamed, filename untouched
# ---------------------------------------------------------------------------


def test_build_movie_path_includes_year_and_tmdb_hint():
    path = build_movie_path(DUNE, "Dune.Part.Two.2024.2160p.REMUX.mkv")
    assert path == config.MOVIE_LIBRARY_ROOT / "Dune Part Two (2024) {tmdb-693134}" / "Dune.Part.Two.2024.2160p.REMUX.mkv"


def test_build_movie_path_omits_year_when_unknown():
    path = build_movie_path(UNTITLED, "release.mkv")
    assert path.parent.name == "Untitled Project {tmdb-1}"


def test_build_movie_path_keeps_original_filename_verbatim():
    path = build_movie_path(DUNE, "dune.part.two.2024.REMUX-SOMEGROUP.mkv")
    assert path.name == "dune.part.two.2024.REMUX-SOMEGROUP.mkv"


def test_build_movie_path_sanitizes_unsafe_characters_in_folder_only():
    identity = MediaIdentity(
        tmdb_id=789, title="Se7en: Director's Cut", original_title="Se7en", release_year=1995, variants=["Se7en"]
    )
    path = build_movie_path(identity, "Se7en.1995.mkv")
    assert ":" not in path.parent.name
    assert path.name == "Se7en.1995.mkv"  # filename never sanitized — it's passed through verbatim


def test_organize_movie_hardlinks_without_renaming_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    source_dir = tmp_path / "downloads" / "Dune Part Two 2024 2160p REMUX-GROUP"
    source_dir.mkdir(parents=True)
    source = source_dir / "Dune Part Two 2024 2160p REMUX-GROUP.mkv"
    source.write_bytes(b"movie bytes")

    target = organize_movie(DUNE, source)

    assert target.name == "Dune Part Two 2024 2160p REMUX-GROUP.mkv"  # unchanged
    assert target.parent.name == "Dune Part Two (2024) {tmdb-693134}"
    assert target.read_bytes() == b"movie bytes"
    assert os.stat(source).st_ino == os.stat(target).st_ino  # a real hardlink


def test_organize_movie_is_idempotent_when_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"first")

    organize_movie(DUNE, source)
    source.write_bytes(b"second")
    target = organize_movie(DUNE, source)

    assert target.read_bytes() == b"second"


# ---------------------------------------------------------------------------
# organize_pack — Stage 13: many files, one per recognizable episode, out
# of a single completed season/complete-series pack torrent.
# ---------------------------------------------------------------------------


def test_organize_pack_places_every_recognizable_episode_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads" / "Lanterns S01 COMPLETE"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.2160p.mkv").write_bytes(b"ep1")
    (downloads / "Lanterns.S01E02.2160p.mkv").write_bytes(b"ep2")
    qbt = FakeQBTClient(
        str(downloads),
        [
            {"name": "Lanterns.S01E01.2160p.mkv", "size": 3},
            {"name": "Lanterns.S01E02.2160p.mkv", "size": 3},
        ],
    )

    placed = organize_pack(LANTERNS, "abc123", qbt)

    assert sorted((season, episode) for season, episode, _ in placed) == [(1, 1), (1, 2)]
    for season, episode, target_path in placed:
        assert target_path == config.TV_LIBRARY_ROOT / "Lanterns (2026) {tmdb-95350}" / "Season 01" / f"Lanterns - s01e{episode:02d}.mkv"
        assert target_path.exists()


def test_organize_pack_skips_files_with_no_recognizable_episode_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.2160p.mkv").write_bytes(b"ep1")
    (downloads / "Lanterns.S01.COMPLETE.nfo").write_bytes(b"nfo")
    (downloads / "extras.mkv").write_bytes(b"extra")  # a video file, but no episode token at all
    qbt = FakeQBTClient(
        str(downloads),
        [
            {"name": "Lanterns.S01E01.2160p.mkv", "size": 3},
            {"name": "Lanterns.S01.COMPLETE.nfo", "size": 3},
            {"name": "extras.mkv", "size": 5},
        ],
    )

    placed = organize_pack(LANTERNS, "abc123", qbt)

    assert len(placed) == 1
    assert placed[0][0:2] == (1, 1)


def test_organize_pack_skips_sample_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.mkv").write_bytes(b"real")
    (downloads / "Lanterns.S01E01.Sample.mkv").write_bytes(b"sample")
    qbt = FakeQBTClient(
        str(downloads),
        [
            {"name": "Lanterns.S01E01.mkv", "size": 4},
            {"name": "Lanterns.S01E01.Sample.mkv", "size": 999},
        ],
    )

    placed = organize_pack(LANTERNS, "abc123", qbt)

    assert len(placed) == 1
    assert placed[0][2].read_bytes() == b"real"


def test_organize_pack_raises_when_torrent_not_found():
    qbt = FakeQBTClient("/downloads", [], info=False)
    with pytest.raises(MediaOrganizerError):
        organize_pack(LANTERNS, "abc123", qbt)


def test_organize_pack_raises_when_nothing_recognizable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    qbt = FakeQBTClient(
        str(tmp_path),
        [
            {"name": "Lanterns.S01.COMPLETE.nfo", "size": 3},
            {"name": "readme.txt", "size": 3},
        ],
    )
    with pytest.raises(MediaOrganizerError):
        organize_pack(LANTERNS, "abc123", qbt)


def test_organize_pack_translates_qbit_container_path_when_configured(tmp_path, monkeypatch):
    """Real deployment shape: qBittorrent's raw download and this
    backend's TV_LIBRARY_ROOT are bind-mounts of the identical host
    folder, each container naming it differently. The real bytes live
    under TV_LIBRARY_ROOT (this process's own view); qBittorrent reports
    a completely different-looking `save_path` string for that same
    folder — organize_pack reads config.QBIT_TV_SAVE_PATH/TV_LIBRARY_ROOT
    directly since a pack is always TV-only content."""
    library_root = tmp_path / "library"
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library_root)
    monkeypatch.setattr(config, "QBIT_TV_SAVE_PATH", "/media/TV Shows")
    real_downloads = library_root / "Lanterns S01 COMPLETE"
    real_downloads.mkdir(parents=True)
    (real_downloads / "Lanterns.S01E01.mkv").write_bytes(b"ep1")
    # qBittorrent's own reported save_path for this exact folder — a
    # different string than `real_downloads` above, to prove translation
    # (not a filesystem coincidence) is what makes this resolve.
    qbt = FakeQBTClient(
        "/media/TV Shows/Lanterns S01 COMPLETE",
        [{"name": "Lanterns.S01E01.mkv", "size": 3}],
    )

    placed = organize_pack(LANTERNS, "abc123", qbt)

    assert len(placed) == 1
    assert placed[0][2].read_bytes() == b"ep1"


def test_organize_pack_handles_subfolder_relative_names(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    (downloads / "Lanterns S01").mkdir(parents=True)
    (downloads / "Lanterns S01" / "Lanterns.S01E03.mkv").write_bytes(b"ep3")
    qbt = FakeQBTClient(str(downloads), [{"name": "Lanterns S01/Lanterns.S01E03.mkv", "size": 3}])

    placed = organize_pack(LANTERNS, "abc123", qbt)

    assert placed == [(1, 3, config.TV_LIBRARY_ROOT / "Lanterns (2026) {tmdb-95350}" / "Season 01" / "Lanterns - s01e03.mkv")]
