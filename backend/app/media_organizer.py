"""Stage 11: places a completed download where Plex reliably recognizes it,
using this app's own already-resolved identity rather than re-parsing the
downloaded release's filename.

`select_video_file` picks the real video file out of a completed torrent's
file list, for either media type. `organize_episode` hardlinks an episode
into Plex's TV layout, renaming the file itself to `SxxEyy`. `organize_movie`
does the movie equivalent, but deliberately renames only the *folder* that
holds the file, not the file itself — Plex primarily matches a movie by its
folder name, and there's no reason to touch a filename that's already
sitting where qBittorrent (and its own seeding) expects it, when a sibling
hardlink achieves the same recognition. Season packs never reach the
episode path — Stage 10's pass-one gate rejects them before an episode
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
from app.normalize import has_token, tokenize
from app.qbt import QBTClient
from app.resolve import MediaIdentity
from app.score import matches_any_variant
from app.tv_resolve import ShowIdentity
from app.tv_score import extract_episode_identity, has_episode_token

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


def find_existing_episode_file(show_identity: ShowIdentity, season: int, episode: int) -> Path | None:
    """Best-effort scan of the whole `TV_LIBRARY_ROOT` for a video file that
    already represents this episode — checked by worker.py's `check_show()`
    (Stage 12) before ever creating a download request for a newly-
    discovered aired episode, so a first-time subscribe to a show that
    already has episodes on disk doesn't try to re-grab them.

    Matches on filename tokens the same whole-token way the search pipeline
    itself does (`matches_any_variant` + `has_episode_token`), not a
    substring guess or this app's own `<Show Title> - sNNeNN` naming
    specifically — a file still sitting under its original scene-release
    name (e.g. a torrent this app added but never successfully organized,
    or one added completely outside this app) is found just as reliably as
    one `organize_episode()` already placed and renamed. Deliberately not
    scoped to the show's own organized subfolder for the same reason: an
    unorganized file has no reason to be there yet.

    Real, not a guarantee: it only looks under `TV_LIBRARY_ROOT`, so an
    episode sitting in qBittorrent's own in-progress/incomplete-downloads
    location, or organized under a completely different library layout,
    won't be found — a named, not-solved gap, same style as this project's
    others. Returns `None` (rather than raising) when the root doesn't
    exist at all — nothing local exercises the real mount, same as every
    other `TV_LIBRARY_ROOT` caller."""
    root = config.TV_LIBRARY_ROOT
    if not root.is_dir():
        return None
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in config.VIDEO_EXTENSIONS:
            continue
        tokens = tokenize(path.stem)
        if matches_any_variant(tokens, show_identity.variants) and has_episode_token(tokens, season, episode):
            return path
    return None


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


def build_movie_path(identity: MediaIdentity, filename: str) -> Path:
    """`<MOVIE_LIBRARY_ROOT>/<Title> ({year}) {tmdb-<id>}/<filename>` —
    same `{tmdb-<id>}` folder hint as TV, but `filename` is passed through
    verbatim (the original release's own name), not rebuilt: only the
    folder needs to say which movie this is for Plex to match it, and
    qBittorrent's own copy already sits under that same filename."""
    title = _sanitize(identity.title)
    folder = f"{title} ({identity.release_year})" if identity.release_year else title
    folder += f" {{tmdb-{identity.tmdb_id}}}"
    return config.MOVIE_LIBRARY_ROOT / folder / filename


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


def organize_pack(show_identity: ShowIdentity, torrent_hash: str, qbt: QBTClient) -> list[tuple[int, int, Path]]:
    """Stage 13: places every individually SxxEyy-identifiable file out of a
    completed season/complete-series pack torrent — an extension of
    `organize_episode`'s own per-file placement (`build_episode_path` +
    hardlink-or-copy), reused here unchanged for each file rather than
    reimplemented, per the plan's "reuse, don't duplicate" call. The only
    genuinely new logic is per-file: which (season, episode) a given file
    represents (`tv_score.extract_episode_identity`, read from the file's
    own name, not from a single fixed request the way `organize_episode`
    is told its season/episode).

    A file with no recognizable episode token — a sample, .nfo, subtitle
    sidecar, or any other unmatched extra — is skipped and logged, never
    treated as an error: a pack's real content is "however many episodes
    it actually turns out to carry," not a fixed count this function
    checks against (Stage 13's "accept what's actually there" resolution
    of its own "partial pack" open decision). Raises `MediaOrganizerError`
    only if the torrent itself is gone, or literally nothing in it is
    recognizable as an episode — the caller-side result maps that onto
    the request's own `"downloaded, not filed"` status, same as
    `organize_episode`.

    Returns one `(season, episode, target_path)` tuple per file actually
    placed; the caller (worker.py) is what maps this back onto per-episode
    `requests` rows and Stage 12's `show_episodes` dedup ledger — this
    function only ever touches the filesystem, never the database."""
    info = qbt.torrent_info(torrent_hash)
    if info is None:
        raise MediaOrganizerError(f"torrent {torrent_hash!r} not found in qBittorrent")
    save_path = info.get("save_path")
    if not save_path:
        raise MediaOrganizerError(f"torrent {torrent_hash!r} has no save_path")

    files = qbt.torrent_files(torrent_hash)
    placed: list[tuple[int, int, Path]] = []
    for f in files:
        name = f["name"]
        path = Path(name)
        if path.suffix.lower() not in config.VIDEO_EXTENSIONS or has_token(path.stem, "sample"):
            continue
        identity_pair = extract_episode_identity(tokenize(path.stem))
        if identity_pair is None:
            logger.info("organize_pack: skipping %r — no recognizable episode token", name)
            continue
        season, episode = identity_pair
        source_path = Path(save_path) / name
        target = build_episode_path(show_identity, season, episode, source_path.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        _link_or_copy(source_path, target)
        placed.append((season, episode, target))

    if not placed:
        raise MediaOrganizerError(
            f"no recognizable episode files found among {len(files)} file(s) in torrent {torrent_hash!r}"
        )
    return placed


def organize_movie(identity: MediaIdentity, source_path: Path) -> Path:
    """Places one movie's already-selected video file (see
    `select_video_file`) into a Plex-recognizable folder — the file's own
    name is kept exactly as-is; only the folder it sits in is renamed to
    `<Title> (<year>) {tmdb-<id>}`. Same hardlink-first, copy-fallback
    placement as `organize_episode`, and the same "only after the torrent
    is fully complete" rule."""
    source_path = Path(source_path)
    target = build_movie_path(identity, source_path.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    _link_or_copy(source_path, target)
    return target
