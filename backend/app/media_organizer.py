"""Stage 11: places a completed episode download where Plex's TV agent
reliably recognizes it, using this app's own already-resolved show/season/
episode identity rather than re-parsing the downloaded release's filename.

Two steps, deliberately separate: `select_video_file` picks the real
episode file out of a completed torrent's file list; `organize_episode`
hardlinks that file into Plex's library layout. Season packs never reach
this module — Stage 10's pass-one gate rejects them before an episode
request is ever created — so "one torrent -> one episode's file" is the
only shape `select_video_file` has to handle; a release whose real file
doesn't fit that assumption is a named, documented gap, same style as
Stage 2's cam-tag gap."""

import errno
import logging
import os
import re
import shutil
from pathlib import Path

from app import config
from app.normalize import has_token
from app.qbt import QBTClient
from app.tv_resolve import ShowIdentity

logger = logging.getLogger("app.media_organizer")

_FS_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')
_WS_RE = re.compile(r"\s+")


class MediaOrganizerError(RuntimeError):
    pass


def _sanitize(name: str) -> str:
    """Strips characters that are unsafe (or, over an SMB-mapped share,
    merely inconvenient) in a filename/folder component — a colon in
    "Star Trek: Discovery" is fine on the NAS's own Linux filesystem but
    breaks the moment the same share is mapped on a Windows client."""
    return _WS_RE.sub(" ", _FS_UNSAFE_RE.sub("", name)).strip()


def select_video_file(qbt: QBTClient, torrent_hash: str) -> Path:
    """The largest file with a known video extension in a completed
    torrent, skipping anything whose name carries a whole "sample" token.
    Raises `MediaOrganizerError` if the torrent is gone or nothing
    qualifies — "fail safe, not best guess": never guess at a non-video
    file just because it happens to be present."""
    info = qbt.torrent_info(torrent_hash)
    if info is None:
        raise MediaOrganizerError(f"torrent {torrent_hash!r} not found in qBittorrent")
    save_path = info.get("save_path")
    if not save_path:
        raise MediaOrganizerError(f"torrent {torrent_hash!r} has no save_path")

    files = qbt.torrent_files(torrent_hash)
    candidates = [
        f
        for f in files
        if Path(f["name"]).suffix.lower() in config.VIDEO_EXTENSIONS
        and not has_token(Path(f["name"]).stem, "sample")
    ]
    if not candidates:
        raise MediaOrganizerError(
            f"no non-sample video file found among {len(files)} file(s) in torrent {torrent_hash!r}"
        )

    winner = max(candidates, key=lambda f: f.get("size", 0))
    return Path(save_path) / winner["name"]


def build_episode_path(show_identity: ShowIdentity, season: int, episode: int, ext: str) -> Path:
    """`<TV_LIBRARY_ROOT>/<Show Title> ({year}) {tmdb-<id>}/Season <NN>/
    <Show Title> - sNNeNN.ext` — Plex's own documented `{tmdb-<id>}`
    folder-naming hint, so Plex is never left to guess which show a folder
    belongs to. Filename is `SxxEyy`-only for v1 (no episode title) — the
    simpler of two equally-valid Plex-recognized shapes; see project.md's
    Stage 11 open decisions."""
    show_title = _sanitize(show_identity.title)
    show_folder = f"{show_title} ({show_identity.first_air_year})" if show_identity.first_air_year else show_title
    show_folder += f" {{tmdb-{show_identity.tmdb_id}}}"
    season_folder = f"Season {season:02d}"
    filename = f"{show_title} - s{season:02d}e{episode:02d}{ext}"
    return config.TV_LIBRARY_ROOT / show_folder / season_folder / filename


def _link_or_copy(source: Path, target: Path) -> None:
    """Hardlinks `source` into `target` — the same safe pattern Sonarr/
    Radarr rely on: qBittorrent's own copy keeps seeding, untouched, while
    Plex sees a second, correctly-named reference to the same bytes at zero
    extra disk cost. Falls back to a logged copy (doubles disk usage) when a
    hardlink genuinely can't be created: `EXDEV` (source/target on different
    filesystems, the case the plan anticipated) or `ENOTSUP`/`EOPNOTSUPP`
    (the filesystem doesn't implement hardlinks at all — found live during
    Stage 11's own validation: macOS's SMB client raises `ENOTSUP` for a
    same-share link, not `EXDEV`, a real gap this fallback didn't originally
    cover). Idempotent: re-organizing an already-placed episode replaces the
    existing link/copy rather than failing on FileExistsError."""
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.link(source, target)
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
        logger.warning(
            "hardlink unsupported (%s -> %s); falling back to a copy, which doubles disk usage",
            source,
            target,
        )
        shutil.copy2(source, target)


def organize_episode(show_identity: ShowIdentity, season: int, episode: int, source_path: Path) -> Path:
    """Places one episode's already-selected video file (see
    `select_video_file`) into Plex's library layout. Rename/place only
    happens here, after the torrent is fully complete — never mid-download,
    so qBittorrent's own resume data and incomplete-file naming are never
    touched."""
    source_path = Path(source_path)
    target = build_episode_path(show_identity, season, episode, source_path.suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    _link_or_copy(source_path, target)
    return target
