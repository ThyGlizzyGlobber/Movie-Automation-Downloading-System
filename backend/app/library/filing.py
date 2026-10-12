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
hardlink achieves the same recognition. Whole seasons and series are
packs.py's."""

import errno
import logging
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from threading import Lock

from app.box import drives
from app.core import config
from app.core.cache import TTLCache
from app.library.audio import audio_default_disposition
from app.library.containers import MOV_TEXT, embedded_artwork_stream_indices, probe_streams, remux
from app.matching.normalize import has_token, normalize_text, tokenize
from app.matching.resolve import MediaIdentity
from app.matching.score import matches_any_variant
from app.matching.tv_resolve import ShowIdentity
from app.matching.tv_score import has_episode_token
from app.qbittorrent.client import QBTClient

logger = logging.getLogger(__name__)


_FS_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


_WS_RE = re.compile(r"\s+")


class MediaOrganizerError(RuntimeError):
    pass


class NoVideoFileError(MediaOrganizerError):
    """A completed torrent contains no file matching `VIDEO_EXTENSIONS` at
    all — the same shape as a fake-release payload (filler .txt/.jpg plus a
    disguised .exe, no real video) rather than an ordinary organize failure.
    A distinct subclass so worker.py's callers can treat this one specific
    case as "purge the torrent outright", not just "downloaded, not
    filed"."""


def _sanitize(name: str) -> str:
    """Strips characters that are unsafe (or, over an SMB-mapped share,
    merely inconvenient) in a filename/folder component — a colon in
    "Star Trek: Discovery" is fine on the NAS's own Linux filesystem but
    breaks the moment the same share is mapped on a Windows client."""
    return _WS_RE.sub(" ", _FS_UNSAFE_RE.sub("", name)).strip()


def translate_qbit_save_path(save_path: str, qbit_root: str | None, local_root: Path) -> Path:
    """qBittorrent reports `save_path` in its own container's filesystem
    namespace, not this backend's — the two run as separate containers,
    each bind-mounting the exact same underlying host directory under a
    path of their own choosing (see config.py's QBIT_TV_SAVE_PATH /
    TV_LIBRARY_ROOT). Rewrites `save_path`'s qBittorrent-side prefix
    (`qbit_root`) to the equivalent path this container can actually open
    (`local_root`).

    Falls back to `save_path` completely unmodified when `qbit_root` isn't
    configured (matches every environment that doesn't run two containers
    against one shared mount — tests, this workstation, a single-container
    deployment) or when `save_path` doesn't actually start with it (an
    unexpected save_path shouldn't be silently mangled into a nonsense
    local path — "fail safe, not best guess": let the caller hit a clean
    file-not-found instead of a wrong guess)."""
    if not qbit_root:
        return Path(save_path)
    try:
        relative = PurePosixPath(save_path).relative_to(PurePosixPath(qbit_root))
    except ValueError:
        return Path(save_path)
    return local_root / Path(*relative.parts)


def select_video_file(
    qbt: QBTClient, torrent_hash: str, qbit_root: str | None = None, local_root: Path | None = None
) -> Path:
    """The largest file with a known video extension in a completed
    torrent, skipping anything whose name carries a whole "sample" token.
    Raises `MediaOrganizerError` if the torrent is gone, or the more
    specific `NoVideoFileError` if it's present but nothing qualifies —
    "fail safe, not best guess": never guess at a non-video file just
    because it happens to be present.

    `qbit_root`/`local_root` translate qBittorrent's own reported
    `save_path` into this container's filesystem namespace (see
    `translate_qbit_save_path`); omitted, the raw `save_path` is used
    as-is, matching every caller that doesn't need translation."""
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
        raise NoVideoFileError(
            f"no non-sample video file found among {len(files)} file(s) in torrent {torrent_hash!r}"
        )

    winner = max(candidates, key=lambda f: f.get("size", 0))
    base = translate_qbit_save_path(save_path, qbit_root, local_root) if local_root is not None else Path(save_path)
    return base / winner["name"]


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
    other `TV_LIBRARY_ROOT` caller.

    On a box, every drive's TV Shows folder is looked through (app/box/drives.py)."""
    for root in drives.library_roots("tv"):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in config.VIDEO_EXTENSIONS:
                continue
            tokens = tokenize(path.stem)
            if matches_any_variant(tokens, show_identity.variants) and has_episode_token(tokens, season, episode):
                return path
    return None


# The show page's episode list asked for a walk of the whole TV library on
# every load, and again every five seconds while anything on it was
# downloading — measured at 0.5-2.3s a time on the NAS, slowing every
# other request it shared the disk with. The walk is kept briefly instead.
# A file that lands meanwhile is still shown, by its request row; one put
# there by hand appears within the TTL.
_TV_LIBRARY_LISTING_TTL_SECONDS = 120


_tv_library_listing = TTLCache(_TV_LIBRARY_LISTING_TTL_SECONDS, max_entries=4)


_tv_library_listing_lock = Lock()


def _tv_library_videos(root: Path) -> list[tuple[Path, list[str]]]:
    """Every video file under `root` with its tokenised name, from a walk
    at most _TV_LIBRARY_LISTING_TTL_SECONDS old."""
    key = (str(root),)
    listing, hit = _tv_library_listing.get(key)
    if hit:
        return listing
    with _tv_library_listing_lock:
        listing, hit = _tv_library_listing.get(key)
        if not hit:
            listing = [
                (path, tokenize(path.stem))
                for path in root.rglob("*")
                if path.suffix.lower() in config.VIDEO_EXTENSIONS and path.is_file()
            ]
            _tv_library_listing.set(key, listing)
    return listing


def find_existing_episode_files(show_identity: ShowIdentity, season: int, episodes: list[int]) -> dict[int, Path]:
    """`find_existing_episode_file` for a whole season at once — one walk
    of `TV_LIBRARY_ROOT`, then each candidate file (one whose name matches
    the show) is checked against every wanted episode number. Backs the
    show page's per-episode status list, which would otherwise re-walk
    the library once per episode. Same matching rules and the same
    named gap (only `TV_LIBRARY_ROOT` is looked at). The walk itself is
    shared and briefly kept — see _tv_library_videos."""
    found: dict[int, Path] = {}
    if not episodes:
        return found
    wanted = set(episodes)
    listing = [entry for root in drives.library_roots("tv") if root.is_dir() for entry in _tv_library_videos(root)]
    for path, tokens in listing:
        if not matches_any_variant(tokens, show_identity.variants):
            continue
        for episode in wanted - found.keys():
            if has_episode_token(tokens, season, episode):
                found[episode] = path
        if wanted <= found.keys():
            break
    return found


# TMDB names an episode it has no real title for "Episode 7" (and, in
# some locales, its translation of that). Appending it would spell out
# the number the filename already carries, so an unnamed episode keeps
# the bare SxxEyy shape instead.
_PLACEHOLDER_EPISODE_NAME_RE = re.compile(r"^(episode|épisode|episodio|folge)\s*\d+$", re.IGNORECASE)


# Long enough for a real title, short enough that the whole filename
# clears the 255-byte limit ext4 and SMB both impose once the show name,
# the SxxEyy and the extension are already spent.
_MAX_EPISODE_TITLE = 120


def episode_title_suffix(title: str | None) -> str:
    """The ` - <Title>` part of an episode filename, or "" when there is
    no title worth adding. Filesystem-sanitised and length-capped, and
    empty for TMDB's "Episode 7" placeholders."""
    if not title:
        return ""
    cleaned = _sanitize(title)
    if not cleaned or _PLACEHOLDER_EPISODE_NAME_RE.match(cleaned):
        return ""
    return f" - {cleaned[:_MAX_EPISODE_TITLE].strip()}"


def build_episode_path(
    show_identity: ShowIdentity, season: int, episode: int, ext: str, title: str | None = None, root: Path | None = None
) -> Path:
    """`<TV_LIBRARY_ROOT>/<Show Title> ({year}) {tmdb-<id>}/Season <NN>/
    <Show Title> - sNNeNN[ - <Episode Title>].ext` — Plex's own
    documented `{tmdb-<id>}` folder-naming hint, so Plex is never left to
    guess which show a folder belongs to.

    The episode title is the second of the two Plex-recognised shapes,
    and closes Stage 11's open decision in favour of it: sNNeNN is what
    Plex actually matches on, so the title is for whoever is reading the
    folder. Optional, because it is worth having only when it is real —
    an unnamed episode, or a title this app could not look up, keeps the
    bare shape rather than gaining a placeholder."""
    show_title = _sanitize(show_identity.title)
    show_folder = f"{show_title} ({show_identity.first_air_year})" if show_identity.first_air_year else show_title
    show_folder += f" {{tmdb-{show_identity.tmdb_id}}}"
    season_folder = f"Season {season:02d}"
    filename = f"{show_title} - s{season:02d}e{episode:02d}{episode_title_suffix(title)}{ext}"
    return (root or config.TV_LIBRARY_ROOT) / show_folder / season_folder / filename


def build_movie_path(identity: MediaIdentity, filename: str, root: Path | None = None) -> Path:
    """`<MOVIE_LIBRARY_ROOT>/<Title> ({year}) {tmdb-<id>}/<filename>` —
    same `{tmdb-<id>}` folder hint as TV, but `filename` is passed through
    verbatim (the original release's own name), not rebuilt: only the
    folder needs to say which movie this is for Plex to match it, and
    qBittorrent's own copy already sits under that same filename."""
    title = _sanitize(identity.title)
    folder = f"{title} ({identity.release_year})" if identity.release_year else title
    folder += f" {{tmdb-{identity.tmdb_id}}}"
    return (root or config.MOVIE_LIBRARY_ROOT) / folder / filename


def link_or_copy(source: Path, target: Path, preferred_audio_language: str | None = None) -> Path:
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
    existing link/copy rather than failing on FileExistsError.

    Stage 15: when `source` carries embedded cover art (a custom scene-
    release poster baked into the container itself, not a separate image
    file — Plex reads this straight off the file and displays it over
    TMDB's own artwork), a hardlink can't fix that since it's the exact
    same bytes. Re-muxes into `target` instead, stripping only the
    artwork stream(s) and copying everything else (video/audio/real
    subtitles/fonts) untouched. Only takes this path when something to
    strip is actually found and the remux succeeds; any other file keeps
    the zero-cost hardlink it always got."""
    # An MP4 is filed as an MKV. The library is kept in one container on
    # purpose: mkvpropedit can edit an MKV's header in place in
    # milliseconds and MP4 has no equivalent, so every MP4 that lands is
    # a file the audio-default fix can never touch again. Rewrapping is
    # a stream copy, so it costs the write and nothing else.
    rewrap = source.suffix.lower() in (".mp4", ".m4v")
    if rewrap:
        target = target.with_suffix(".mkv")

    if target.exists() or target.is_symlink():
        target.unlink()

    # Every reason to write a real file rather than hardlink, taken in
    # one trip: there is no point remuxing twice. The hardlink is also
    # what makes an edit-in-place tool like mkvpropedit unusable here —
    # the library copy and qBittorrent's are the same bytes, so editing
    # one corrupts the torrent mid-seed.
    strip_indices = embedded_artwork_stream_indices(source)
    disposition = audio_default_disposition(source, preferred_audio_language) if preferred_audio_language else []
    extra_args = list(disposition)
    if rewrap:
        # MP4 keeps text subtitles as mov_text, which Matroska will not
        # take and which aborts the whole remux under -c copy.
        streams = probe_streams(source) or []
        if any(stream.get("codec_name") == MOV_TEXT for stream in streams):
            extra_args += ["-c:s", "srt"]
    if (rewrap or strip_indices or disposition) and remux(source, target, strip_indices, extra_args):
        if rewrap:
            logger.info("rewrapped %r as %r while organizing", source.name, target.name)
        if strip_indices:
            logger.info("stripped %d embedded-artwork stream(s) from %r while organizing", len(strip_indices), str(source))
        if disposition:
            logger.info("made the %s audio track default on %r while organizing", preferred_audio_language, str(target))
        return target
    if rewrap:
        # The rewrap is the one of the three that can't fall back: a
        # hardlink here would file an MP4 under a .mkv name, which is
        # worse than either honest outcome.
        target = target.with_suffix(source.suffix)
        logger.warning("could not rewrap %r as MKV; filing it as-is", source.name)
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
    return target


def organize_episode(
    show_identity: ShowIdentity,
    season: int,
    episode: int,
    source_path: Path,
    title: str | None = None,
    preferred_audio_language: str | None = None,
) -> Path:
    """Places one episode's already-selected video file (see
    `select_video_file`) into Plex's library layout. Rename/place only
    happens here, after the torrent is fully complete — never mid-download,
    so qBittorrent's own resume data and incomplete-file naming are never
    touched.

    `title` goes into the filename when given; the caller looks it up,
    keeping this module's only inputs the filesystem and qBittorrent."""
    source_path = Path(source_path)
    # Filed on the drive it was downloaded to, so it's a hardlink.
    root = drives.library_root("tv", near=source_path)
    target = build_episode_path(show_identity, season, episode, source_path.suffix, title, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    return link_or_copy(source_path, target, preferred_audio_language)


def find_release_dir(root: Path, release_name: str) -> Path | None:
    """The release's own folder under `root`, found by name when
    qBittorrent can no longer be asked where it put it.

    Matched on normalized tokens, not on the string: the name this app
    stored is the indexer's display name ("Supernatural S14 ITA ENG
    1080p AMZN WEBRip AAC x265-Pir8") while the folder on disk carries
    the torrent's own dotted name ("Supernatural.S14.ITA.ENG.1080p.
    AMZN.WEBRip.AAC.x265-Pir8") — same words, different separators, so
    comparing either form directly misses. `normalize_text` flattens
    both to the same string. Returns None rather than guessing when
    nothing matches exactly: a near-match here would file somebody
    else's download under this show."""
    wanted = normalize_text(release_name)
    if not wanted:
        return None
    try:
        children = sorted(root.iterdir())
    except OSError as exc:
        logger.warning("find_release_dir: cannot list %s (%s)", root, exc)
        return None
    for child in children:
        if child.is_dir() and normalize_text(child.name) == wanted:
            return child
    return None


def organize_movie(
    identity: MediaIdentity, source_path: Path, preferred_audio_language: str | None = None
) -> Path:
    """Places one movie's already-selected video file (see
    `select_video_file`) into a Plex-recognizable folder — the file's own
    name is kept exactly as-is; only the folder it sits in is renamed to
    `<Title> (<year>) {tmdb-<id>}`. Same hardlink-first, copy-fallback
    placement as `organize_episode`, and the same "only after the torrent
    is fully complete" rule."""
    source_path = Path(source_path)
    target = build_movie_path(identity, source_path.name, drives.library_root("movie", near=source_path))
    target.parent.mkdir(parents=True, exist_ok=True)
    return link_or_copy(source_path, target, preferred_audio_language)
