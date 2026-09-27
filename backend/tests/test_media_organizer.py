import errno
import json
import os
import subprocess
from pathlib import Path

import pytest

from app import config, media_organizer
from app.media_organizer import (
    MediaOrganizerError,
    NoVideoFileError,
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


def test_select_video_file_finds_the_main_title_in_a_bdmv_remux_release():
    """Confirmed live: "Tron.Legacy.2010.2160p.BDMV.Remux...-DaTmoSX"'s real
    payload is BDMV/STREAM/00000.m2ts, plus other much-smaller playlist
    items for extras/trailers — .m2ts missing from VIDEO_EXTENSIONS made
    this look like "no video file at all" and got the whole torrent purged
    as a fake release. The existing largest-file heuristic is otherwise
    sufficient here: the main feature is always by far the largest stream."""
    qbt = FakeQBTClient(
        "/downloads",
        [
            {
                "name": "Tron.Legacy.2010.2160p.BDMV.Remux.DV.HDR.HEVC.TrueHD.7.1-DaTmoSX/BDMV/STREAM/00000.m2ts",
                "size": 60_000_000_000,
            },
            {
                "name": "Tron.Legacy.2010.2160p.BDMV.Remux.DV.HDR.HEVC.TrueHD.7.1-DaTmoSX/BDMV/STREAM/00001.m2ts",
                "size": 200_000_000,
            },
            {
                "name": "Tron.Legacy.2010.2160p.BDMV.Remux.DV.HDR.HEVC.TrueHD.7.1-DaTmoSX/BDMV/index.bdmv",
                "size": 1000,
            },
        ],
    )
    result = select_video_file(qbt, "abc123")
    assert result.name == "00000.m2ts"


def test_select_video_file_raises_when_torrent_not_found():
    qbt = FakeQBTClient("/downloads", [], info=False)
    with pytest.raises(MediaOrganizerError):
        select_video_file(qbt, "abc123")


def test_select_video_file_raises_when_nothing_qualifies():
    qbt = FakeQBTClient("/downloads", [{"name": "Lanterns.S01E01.nfo", "size": 1000}])
    with pytest.raises(MediaOrganizerError):
        select_video_file(qbt, "abc123")


def test_select_video_file_raises_no_video_file_error_specifically_when_nothing_qualifies():
    """A torrent with zero video-extension files at all (a filler .txt/.jpg
    plus a disguised .exe, the real-world fake-release shape confirmed live
    2026-09-14) raises the specific `NoVideoFileError` subclass, not just
    the generic `MediaOrganizerError` — worker.py keys off this specific
    type to purge the torrent outright rather than leaving it as
    "downloaded, not filed"."""
    qbt = FakeQBTClient(
        "/downloads",
        [
            {"name": "release.exe", "size": 964_900_000},
            {"name": "site.jpg", "size": 38_000},
            {"name": "readme.txt", "size": 848},
        ],
    )
    with pytest.raises(NoVideoFileError):
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
# _link_or_copy — Stage 15: stripping embedded cover art (an ffmpeg
# "attached picture" video stream) at organize time, real ffmpeg-generated
# media rather than fake byte content, since this specifically exercises
# ffprobe/ffmpeg subprocess behavior that fake content can't.
# ---------------------------------------------------------------------------


def _ffprobe_streams(path: Path) -> list[dict]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_entries", "stream=codec_type:stream_tags=mimetype,filename:stream_disposition=attached_pic",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout).get("streams", [])


def _make_plain_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )


def _make_video_with_embedded_cover_art(path: Path, tmp_path: Path) -> None:
    """Confirmed live: ffmpeg's own `-attach` mechanism for an *image*
    file in an MKV container surfaces to ffprobe as a `video`-type stream
    with `disposition.attached_pic == 1` — the same convention mp4/m4a
    cover art uses — not as a distinct `attachment`-type stream (ffmpeg
    reserves that classification for non-image attachments like subtitle
    fonts). This exercises `_embedded_artwork_stream_indices`'s
    `attached_pic` branch, the one that actually fires for real embedded
    cover art regardless of container."""
    base = tmp_path / "_base.mkv"
    cover = tmp_path / "_cover.jpg"
    _make_plain_video(base)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=32x32", "-frames:v", "1", str(cover)],
        capture_output=True,
        timeout=15,
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(base),
            "-attach", str(cover), "-metadata:s:t:0", "mimetype=image/jpeg", "-metadata:s:t:0", "filename=cover.jpg",
            "-c", "copy", str(path),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )


def test_organize_movie_strips_embedded_cover_art_but_keeps_the_real_video_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "Some.Scene.Release.2024.mkv"
    _make_video_with_embedded_cover_art(source, tmp_path)
    assert any(s.get("disposition", {}).get("attached_pic") == 1 for s in _ffprobe_streams(source))  # sanity check

    target = organize_movie(DUNE, source)

    streams = _ffprobe_streams(target)
    assert not any(s.get("disposition", {}).get("attached_pic") == 1 for s in streams)
    assert any(
        s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in streams
    )  # the real video survived, not just stripped to nothing
    assert os.stat(source).st_ino != os.stat(target).st_ino  # a remux happened, not a hardlink of the tainted file


def test_organize_movie_still_hardlinks_a_real_video_with_no_embedded_artwork(tmp_path, monkeypatch):
    """Confirms the new ffprobe check doesn't make every organize call pay
    for a needless remux — a normal video with nothing to strip still gets
    the original zero-cost hardlink."""
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", tmp_path / "library")
    source = tmp_path / "Some.Clean.Release.2024.mkv"
    _make_plain_video(source)

    target = organize_movie(DUNE, source)

    assert os.stat(source).st_ino == os.stat(target).st_ino


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


# The disk fallback: qBittorrent can be set to drop a torrent minutes
# after it finishes seeding, data left in place, which used to make any
# organize failure permanent — nothing could read the file list again.


def test_organize_pack_reads_from_disk_when_torrent_is_gone(tmp_path, monkeypatch):
    library = tmp_path / "library"
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library)
    # The download sits in the library root, where qBittorrent saves it.
    release = library / "Lanterns.S01.COMPLETE.1080p.WEBRip-GRP"
    release.mkdir(parents=True)
    (release / "Lanterns.S01E01.1080p.mkv").write_bytes(b"ep1")
    (release / "Lanterns.S01E02.1080p.mkv").write_bytes(b"ep2")
    qbt = FakeQBTClient("/downloads", [], info=False)

    # The stored name is the indexer's spaced display name; the folder on
    # disk is the torrent's dotted one. Matching has to survive that.
    placed = organize_pack(LANTERNS, "abc123", qbt, "Lanterns S01 COMPLETE 1080p WEBRip-GRP")

    assert sorted((season, episode) for season, episode, _ in placed) == [(1, 1), (1, 2)]
    for _, _, target_path in placed:
        assert target_path.exists()


def test_organize_pack_disk_fallback_needs_a_release_name(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    qbt = FakeQBTClient("/downloads", [], info=False)
    with pytest.raises(MediaOrganizerError, match="no release name"):
        organize_pack(LANTERNS, "abc123", qbt)


def test_organize_pack_disk_fallback_says_so_when_the_folder_is_gone_too(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir(parents=True)
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library)
    qbt = FakeQBTClient("/downloads", [], info=False)
    with pytest.raises(MediaOrganizerError, match="no folder matching"):
        organize_pack(LANTERNS, "abc123", qbt, "Lanterns S01 COMPLETE 1080p WEBRip-GRP")


def test_organize_pack_disk_fallback_does_not_match_a_different_release(tmp_path, monkeypatch):
    """A near-match must not file someone else's download under this
    show — the folder has to be the same words, not merely similar."""
    library = tmp_path / "library"
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library)
    other = library / "Lanterns.S02.COMPLETE.1080p.WEBRip-GRP"
    other.mkdir(parents=True)
    (other / "Lanterns.S02E01.1080p.mkv").write_bytes(b"ep1")
    qbt = FakeQBTClient("/downloads", [], info=False)
    with pytest.raises(MediaOrganizerError, match="no folder matching"):
        organize_pack(LANTERNS, "abc123", qbt, "Lanterns S01 COMPLETE 1080p WEBRip-GRP")


def test_organize_pack_prefers_the_torrent_when_it_is_still_there(tmp_path, monkeypatch):
    """The fallback is a fallback: a live torrent still decides where the
    files are, even when a same-named folder sits in the library root."""
    library = tmp_path / "library"
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library)
    downloads = tmp_path / "elsewhere" / "Lanterns.S01.COMPLETE"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.mkv").write_bytes(b"from the torrent")
    decoy = library / "Lanterns.S01.COMPLETE"
    decoy.mkdir(parents=True)
    (decoy / "Lanterns.S01E01.mkv").write_bytes(b"from the decoy")
    qbt = FakeQBTClient(str(downloads), [{"name": "Lanterns.S01E01.mkv", "size": 3}])

    placed = organize_pack(LANTERNS, "abc123", qbt, "Lanterns S01 COMPLETE")

    assert len(placed) == 1
    assert placed[0][2].read_bytes() == b"from the torrent"


def test_organize_pack_disk_fallback_reads_the_real_italian_pack(tmp_path, monkeypatch):
    """Both halves of the Supernatural S14 failure at once: the torrent
    long gone, and every file named 14xNN rather than S14ENN."""
    library = tmp_path / "library"
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", library)
    release = library / "Supernatural.S14.ITA.ENG.1080p.AMZN.WEBRip.AAC.x265-Pir8"
    release.mkdir(parents=True)
    for episode, titolo in ((1, "Straniero.In.Terra.Straniera"), (6, "Ottimismo"), (20, "Moriah")):
        name = f"Supernatural.14x{episode:02d}.{titolo}.ITA.ENG.1080p.AMZN.WEBRip.AAC.x265-Pir8.mkv"
        (release / name).write_bytes(b"ep")
    supernatural = ShowIdentity(
        tmdb_id=1622,
        title="Supernatural",
        original_title="Supernatural",
        variants=["Supernatural"],
        first_air_year=2005,
    )
    qbt = FakeQBTClient("/downloads", [], info=False)

    placed = organize_pack(
        supernatural, "26f75a7ad457d6c98dece41cc41ed840311ad9d1", qbt,
        "Supernatural S14 ITA ENG 1080p AMZN WEBRip AAC x265-Pir8",
    )

    assert sorted((season, episode) for season, episode, _ in placed) == [(14, 1), (14, 6), (14, 20)]
    for _, episode, target_path in placed:
        assert target_path.name == f"Supernatural - s14e{episode:02d}.mkv"
        assert target_path.exists()


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


def test_organize_pack_raises_no_video_file_error_when_zero_video_files(tmp_path, monkeypatch):
    """Same fake-release shape as select_video_file's own test — zero
    video-extension files anywhere in the pack raises the specific
    `NoVideoFileError`, which worker.py treats as grounds to purge the
    torrent outright."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    qbt = FakeQBTClient(
        str(tmp_path),
        [
            {"name": "Lanterns.S01.COMPLETE.nfo", "size": 3},
            {"name": "readme.txt", "size": 3},
        ],
    )
    with pytest.raises(NoVideoFileError):
        organize_pack(LANTERNS, "abc123", qbt)


def test_organize_pack_raises_plain_error_not_no_video_file_error_when_video_files_are_just_unparseable(
    tmp_path, monkeypatch
):
    """A real video file that's present but whose name this app's parser
    can't pin to a season/episode (unusual release naming) is a genuine
    "can't file this yet" case, not a fake-release signal — must raise
    plain `MediaOrganizerError`, never the `NoVideoFileError` subclass,
    so worker.py leaves it recoverable ("downloaded, not filed") instead
    of deleting someone's real download."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "some_weirdly_named_video.mkv").write_bytes(b"real video, unparseable name")
    qbt = FakeQBTClient(
        str(downloads),
        [{"name": "some_weirdly_named_video.mkv", "size": 30}],
    )
    with pytest.raises(MediaOrganizerError) as exc_info:
        organize_pack(LANTERNS, "abc123", qbt)
    assert not isinstance(exc_info.value, NoVideoFileError)


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


# ---------------------------------------------------------------------------
# Episode titles in the filename — the second of Plex's two recognised
# shapes, closing Stage 11's open decision. sNNeNN is what Plex matches
# on, so the title is for whoever reads the folder.
# ---------------------------------------------------------------------------


def test_build_episode_path_appends_the_episode_title(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    path = build_episode_path(LANTERNS, 1, 4, ".mkv", "The Witness")
    assert path.name == "Lanterns - s01e04 - The Witness.mkv"


def test_build_episode_path_without_a_title_is_unchanged(tmp_path, monkeypatch):
    """The bare shape is still what an untitled episode gets, so nothing
    already filed changes meaning."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    assert build_episode_path(LANTERNS, 1, 4, ".mkv").name == "Lanterns - s01e04.mkv"
    assert build_episode_path(LANTERNS, 1, 4, ".mkv", None).name == "Lanterns - s01e04.mkv"
    assert build_episode_path(LANTERNS, 1, 4, ".mkv", "   ").name == "Lanterns - s01e04.mkv"


def test_build_episode_path_skips_tmdb_placeholder_titles(tmp_path, monkeypatch):
    """TMDB calls an untitled episode "Episode 7" — spelling out the
    number the filename already carries."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    for placeholder in ("Episode 7", "episode 7", "Episodio 7", "Folge 7"):
        assert build_episode_path(LANTERNS, 1, 7, ".mkv", placeholder).name == "Lanterns - s01e07.mkv"


def test_build_episode_path_sanitises_a_title_for_the_filesystem(tmp_path, monkeypatch):
    """Same treatment the show name gets: a colon is fine on the NAS and
    breaks the moment the share is mapped on Windows."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    path = build_episode_path(LANTERNS, 3, 1, ".mkv", "Three Robots: Exit Strategies")
    assert path.name == "Lanterns - s03e01 - Three Robots Exit Strategies.mkv"


def test_build_episode_path_caps_a_very_long_title(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path)
    path = build_episode_path(LANTERNS, 1, 1, ".mkv", "Word " * 100)
    assert len(path.name.encode()) < 255


def test_organize_pack_names_each_file_from_the_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "Lanterns.S01E01.mkv").write_bytes(b"ep1")
    (downloads / "Lanterns.S01E02.mkv").write_bytes(b"ep2")
    qbt = FakeQBTClient(
        str(downloads),
        [{"name": "Lanterns.S01E01.mkv", "size": 3}, {"name": "Lanterns.S01E02.mkv", "size": 3}],
    )
    titles = {(1, 1): "Sonnie's Edge", (1, 2): None}

    placed = organize_pack(LANTERNS, "abc123", qbt, None, lambda s, e, *_: (s, e, titles.get((s, e))))

    names = sorted(p.name for _, _, p in placed)
    # The one TMDB has no name for keeps the bare shape rather than
    # holding up the rest of the pack.
    # The apostrophe survives — _sanitize only strips what a filesystem
    # or an SMB client actually objects to.
    assert names == ["Lanterns - s01e01 - Sonnie's Edge.mkv", "Lanterns - s01e02.mkv"]


def test_organize_pack_files_an_appended_special_into_season_00(tmp_path, monkeypatch):
    """The Invincible shape: a season-1 pack carrying the Atom Eve
    special as S01E09, where TMDB has season 1 stopping at 8 and the
    special at S00E01. Filed literally it is an episode Plex has never
    heard of; the placement lookup moves it."""
    monkeypatch.setattr(config, "TV_LIBRARY_ROOT", tmp_path / "library")
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    for n in (8, 9):
        (downloads / f"Lanterns.S01E{n:02d}.mkv").write_bytes(b"x")
    qbt = FakeQBTClient(
        str(downloads),
        [{"name": f"Lanterns.S01E{n:02d}.mkv", "size": 1} for n in (8, 9)],
    )
    placement = {(1, 8): (1, 8, "The Last One"), (1, 9): (0, 1, "The Special")}

    placed = organize_pack(LANTERNS, "abc123", qbt, None, lambda s, e, *_: placement[(s, e)])

    by_key = {(s, e): p for s, e, p in placed}
    assert by_key[(1, 8)].parent.name == "Season 01"
    special = by_key[(0, 1)]
    assert special.parent.name == "Season 00"
    assert special.name == "Lanterns - s00e01 - The Special.mkv"
    # The tuple reports where the file went, not what the release called
    # it — the caller's ledger has to record the real place.
    assert (1, 9) not in by_key


# ---------------------------------------------------------------------------
# Default audio track — Plex's own audio preference is per account, so a
# release whose French track is flagged default plays French for every
# guest on the server. The flag lives in the file; this is where it moves.
# ---------------------------------------------------------------------------


def _ffprobe_audio(streams):
    """ffprobe's -select_streams a JSON, shaped as the real one is."""
    return json.dumps({"streams": streams})


def _audio(index, lang, default=0):
    return {"index": index, "tags": {"language": lang} if lang else {}, "disposition": {"default": default}}


def _fake_probe(monkeypatch, payload, returncode=0):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=payload, stderr="")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)


def test_the_preferred_track_is_made_default_when_another_one_holds_it(monkeypatch, tmp_path):
    """The reported case: a French release of an English-language film,
    French flagged default, English sitting behind it."""
    _fake_probe(monkeypatch, _ffprobe_audio([_audio(1, "fre", default=1), _audio(2, "eng")]))

    args = media_organizer._audio_default_disposition(tmp_path / "f.mkv", "en")

    assert args == ["-disposition:a:0", "0", "-disposition:a:1", "default"]


def test_nothing_is_rewritten_when_the_preferred_track_is_already_default(monkeypatch, tmp_path):
    """Most releases are already right, and this is what keeps them on
    their free hardlink instead of paying for a remux to change nothing."""
    _fake_probe(monkeypatch, _ffprobe_audio([_audio(1, "eng", default=1), _audio(2, "fre")]))

    assert media_organizer._audio_default_disposition(tmp_path / "f.mkv", "en") == []


def test_a_single_audio_track_is_left_alone(monkeypatch, tmp_path):
    """Nothing to choose between, whatever it is tagged."""
    _fake_probe(monkeypatch, _ffprobe_audio([_audio(1, "fre", default=1)]))

    assert media_organizer._audio_default_disposition(tmp_path / "f.mkv", "en") == []


def test_a_file_with_no_track_in_that_language_is_left_alone(monkeypatch, tmp_path):
    """A Japanese film with no English dub keeps its own audio rather
    than being pointed at a track that isn't there."""
    _fake_probe(monkeypatch, _ffprobe_audio([_audio(1, "jpn", default=1), _audio(2, "kor")]))

    assert media_organizer._audio_default_disposition(tmp_path / "f.mkv", "en") == []


def test_an_untagged_track_is_not_assumed_to_be_the_preferred_one(monkeypatch, tmp_path):
    _fake_probe(monkeypatch, _ffprobe_audio([_audio(1, "fre", default=1), _audio(2, None)]))

    assert media_organizer._audio_default_disposition(tmp_path / "f.mkv", "en") == []


def test_two_tracks_both_flagged_default_are_reduced_to_one(monkeypatch, tmp_path):
    """Real files do carry this, and a player's behaviour with two
    defaults is its own business — so say it once, explicitly."""
    _fake_probe(monkeypatch, _ffprobe_audio([_audio(1, "eng", default=1), _audio(2, "fre", default=1)]))

    args = media_organizer._audio_default_disposition(tmp_path / "f.mkv", "en")

    assert args == ["-disposition:a:0", "default", "-disposition:a:1", "0"]


def test_a_failed_probe_leaves_the_file_alone(monkeypatch, tmp_path):
    """Same philosophy as the artwork probe above it: never block
    organizing a file over this."""
    _fake_probe(monkeypatch, "", returncode=1)

    assert media_organizer._audio_default_disposition(tmp_path / "f.mkv", "en") == []


# ---------------------------------------------------------------------------
# Backfill — the same decision, applied to files already in the library.
# ---------------------------------------------------------------------------


def _mkv(path: Path, tracks) -> Path:
    """A file that only has to exist and end in .mkv; the probe is faked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really an mkv")
    return path


def _probe_returns(monkeypatch, by_name):
    """ffprobe answers per filename, so one scan can mix several shapes."""

    def run(cmd, **kwargs):
        path = Path(cmd[-1])
        streams = [
            {"tags": {"language": lang} if lang else {}, "disposition": {"default": 1 if default else 0}}
            for lang, default in by_name[path.name]
        ]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"streams": streams}), stderr="")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)


def test_the_backfill_plans_the_same_change_the_organiser_would(monkeypatch, tmp_path):
    _mkv(tmp_path / "French Release.mkv", None)
    _probe_returns(monkeypatch, {"French Release.mkv": [("fre", True), ("eng", False)]})

    plans = media_organizer.plan_audio_default_fix(tmp_path, "en")

    assert [(p.path.name, p.wanted, p.skipped) for p in plans] == [("French Release.mkv", 1, None)]
    assert plans[0].summary == "[fre* eng]"


def test_a_file_that_is_already_right_is_not_in_the_plan_at_all(monkeypatch, tmp_path):
    """Not reported as skipped either — "nothing to do" is not an
    exception, and a report listing every correct file in the library
    would bury the ones that aren't."""
    _mkv(tmp_path / "Fine.mkv", None)
    _probe_returns(monkeypatch, {"Fine.mkv": [("eng", True), ("fre", False)]})

    assert media_organizer.plan_audio_default_fix(tmp_path, "en") == []


def test_a_still_hardlinked_file_is_skipped_rather_than_edited(monkeypatch, tmp_path):
    """mkvpropedit rewrites the header in place, and the organiser
    hardlinks — so more than one link means qBittorrent is very likely
    seeding these exact bytes, and editing them fails its next re-check.
    This is the guard that makes the whole command safe to run."""
    seeding = _mkv(tmp_path / "Seeding.mkv", None)
    os.link(seeding, tmp_path / "torrent-copy.mkv")
    _probe_returns(monkeypatch, {
        "Seeding.mkv": [("fre", True), ("eng", False)],
        "torrent-copy.mkv": [("fre", True), ("eng", False)],
    })

    plans = media_organizer.plan_audio_default_fix(tmp_path, "en")

    assert plans and all(p.wanted is None for p in plans)
    assert all("hardlinked" in p.skipped for p in plans)


def test_a_non_mkv_is_skipped_loudly_rather_than_dropped(monkeypatch, tmp_path):
    """mkvpropedit is Matroska-only. Saying so beats leaving the file out
    of the report, which would read as "this one was fine"."""
    _mkv(tmp_path / "Movie.mp4", None)
    _probe_returns(monkeypatch, {"Movie.mp4": [("fre", True), ("eng", False)]})

    plans = media_organizer.plan_audio_default_fix(tmp_path, "en")

    assert len(plans) == 1
    assert plans[0].wanted is None
    assert "MKV" in plans[0].skipped


def test_the_backfill_and_the_organiser_cannot_disagree(monkeypatch, tmp_path):
    """Both read preferred_audio_track. The point of the split is that
    one file can't start being fixed one way on the way in and another
    way afterwards."""
    source = _mkv(tmp_path / "Both.mkv", None)
    _probe_returns(monkeypatch, {"Both.mkv": [("ita", True), ("eng", False)]})

    organiser = media_organizer._audio_default_disposition(source, "en")
    backfill = media_organizer.plan_audio_default_fix(tmp_path, "en")[0]

    assert organiser == ["-disposition:a:0", "0", "-disposition:a:1", "default"]
    assert backfill.wanted == 1


def test_apply_builds_the_track_selectors_mkvpropedit_expects(monkeypatch, tmp_path):
    """mkvpropedit numbers tracks from 1 within their own type, so the
    second audio track is `track:a2`. Off by one here would silently flag
    the wrong track."""
    path = _mkv(tmp_path / "X.mkv", None)
    plan = media_organizer.AudioFixPlan(
        path=path,
        tracks=[media_organizer.AudioTrack("fre", True), media_organizer.AudioTrack("eng", False)],
        wanted=1,
        skipped=None,
    )
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)

    assert media_organizer.apply_audio_default_fix(plan) is None
    assert seen["cmd"][2:] == [
        "--edit", "track:a1", "--set", "flag-default=0",
        "--edit", "track:a2", "--set", "flag-default=1",
    ]


def test_a_missing_mkvpropedit_says_so_instead_of_crashing(monkeypatch, tmp_path):
    path = _mkv(tmp_path / "X.mkv", None)
    plan = media_organizer.AudioFixPlan(
        path=path,
        tracks=[media_organizer.AudioTrack("fre", True), media_organizer.AudioTrack("eng", False)],
        wanted=1,
        skipped=None,
    )

    def run(cmd, **kwargs):
        raise FileNotFoundError("mkvpropedit")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)

    assert "mkvtoolnix" in media_organizer.apply_audio_default_fix(plan)


# ---------------------------------------------------------------------------
# MP4 -> MKV. A container change, never a re-encode — and the step after
# it deletes the original, which is what these are really about.
# ---------------------------------------------------------------------------


def _streams_json(kinds, subtitle_codec=None):
    out = []
    for i, kind in enumerate(kinds):
        stream = {"index": i, "codec_type": kind}
        if kind == "subtitle" and subtitle_codec:
            stream["codec_name"] = subtitle_codec
        out.append(stream)
    return json.dumps({"streams": out})


def test_a_successful_rewrap_removes_the_mp4(monkeypatch, tmp_path):
    source = tmp_path / "Show - s01e01.mp4"
    source.write_bytes(b"mp4")

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, stdout=_streams_json(["video", "audio", "audio"]), stderr="")
        Path(cmd[-1]).write_bytes(b"mkv")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)
    plan = media_organizer.plan_container_conversion(tmp_path)[0]

    assert media_organizer.apply_container_conversion(plan) is None
    assert plan.target.exists()
    assert not source.exists()


def test_a_failed_rewrap_leaves_the_mp4_exactly_as_it_was(monkeypatch, tmp_path):
    source = tmp_path / "Show - s01e01.mp4"
    source.write_bytes(b"mp4")

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, stdout=_streams_json(["video", "audio"]), stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ffmpeg exploded")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)
    plan = media_organizer.plan_container_conversion(tmp_path)[0]

    assert media_organizer.apply_container_conversion(plan) is not None
    assert source.read_bytes() == b"mp4"
    assert not plan.target.exists()
    assert list(tmp_path.iterdir()) == [source]


def test_a_rewrap_that_lost_a_track_does_not_delete_the_original(monkeypatch, tmp_path):
    """The guard that matters. ffmpeg can exit clean having written a
    file that is missing a stream, and the step after this one deletes
    the only other copy — so the result is counted, not trusted."""
    source = tmp_path / "Show - s01e01.mp4"
    source.write_bytes(b"mp4")
    calls = {"n": 0}

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            calls["n"] += 1
            # Two audio streams going in, one coming back out.
            kinds = ["video", "audio", "audio"] if calls["n"] <= 2 else ["video", "audio"]
            return subprocess.CompletedProcess(cmd, 0, stdout=_streams_json(kinds), stderr="")
        Path(cmd[-1]).write_bytes(b"truncated")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)
    plan = media_organizer.plan_container_conversion(tmp_path)[0]

    error = media_organizer.apply_container_conversion(plan)

    assert "stream count changed" in error
    assert source.exists()
    assert not plan.target.exists()


def test_mov_text_subtitles_are_converted_rather_than_copied(monkeypatch, tmp_path):
    """Matroska will not take mov_text, and -c copy on one aborts the
    whole remux — so an MP4 with subtitles would simply never convert."""
    (tmp_path / "Show - s01e01.mp4").write_bytes(b"mp4")
    seen = {}

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=_streams_json(["video", "audio", "subtitle"], "mov_text"), stderr=""
            )
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"mkv")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)
    plan = media_organizer.plan_container_conversion(tmp_path)[0]
    media_organizer.apply_container_conversion(plan)

    assert "-c:s" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("-c:s") + 1] == "srt"


def test_an_existing_mkv_of_the_same_name_stops_the_conversion(monkeypatch, tmp_path):
    """Overwriting it would be destroying a file nobody asked about."""
    (tmp_path / "Show - s01e01.mp4").write_bytes(b"mp4")
    (tmp_path / "Show - s01e01.mkv").write_bytes(b"already here")

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=_streams_json(["video", "audio"]), stderr="")

    monkeypatch.setattr(media_organizer.subprocess, "run", run)
    plan = media_organizer.plan_container_conversion(tmp_path)[0]

    assert plan.skipped is not None
    assert (tmp_path / "Show - s01e01.mkv").read_bytes() == b"already here"


def test_mkvs_are_left_out_of_the_plan_entirely(monkeypatch, tmp_path):
    (tmp_path / "Already Fine.mkv").write_bytes(b"mkv")

    assert media_organizer.plan_container_conversion(tmp_path) == []
