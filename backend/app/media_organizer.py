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
import json
import logging
import os
import re
import shutil
import subprocess
from typing import NamedTuple
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from app import config
from app.language import audio_quality_key, is_audio_language
from app.normalize import extract_episode_identity, has_token, normalize_text, tokenize
from app.qbt import QBTClient
from app.resolve import MediaIdentity
from app.score import matches_any_variant
from app.tv_resolve import ShowIdentity
from app.tv_score import has_episode_token

logger = logging.getLogger("app.media_organizer")

_FS_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')
_WS_RE = re.compile(r"\s+")
_COVER_NAME_RE = re.compile(r"\b(cover|poster|folder|fanart|banner)\b", re.IGNORECASE)


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


def find_existing_episode_files(show_identity: ShowIdentity, season: int, episodes: list[int]) -> dict[int, Path]:
    """`find_existing_episode_file` for a whole season at once — one walk
    of `TV_LIBRARY_ROOT`, then each candidate file (one whose name matches
    the show) is checked against every wanted episode number. Backs the
    show page's per-episode status list, which would otherwise re-walk
    the library once per episode. Same matching rules and the same
    named gap (only `TV_LIBRARY_ROOT` is looked at)."""
    found: dict[int, Path] = {}
    root = config.TV_LIBRARY_ROOT
    if not root.is_dir() or not episodes:
        return found
    wanted = set(episodes)
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in config.VIDEO_EXTENSIONS:
            continue
        tokens = tokenize(path.stem)
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


def probe_duration_minutes(path: Path) -> float | None:
    """The file's own runtime in minutes, or None if ffprobe can't say.

    Used only to tell two candidate specials apart, so a failure here
    costs a tiebreak and nothing else — never an organize. Deliberately
    a second, narrower ffprobe than the artwork check: that one runs on
    every file at link time, this one only on the handful an episode
    number could not place on its own."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return float(result.stdout.strip()) / 60
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.info("ffprobe gave no duration for %r (%s)", str(path), exc)
        return None


def build_episode_path(
    show_identity: ShowIdentity, season: int, episode: int, ext: str, title: str | None = None
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


def _embedded_artwork_stream_indices(source: Path) -> list[int]:
    """ffprobe's stream list for `source`, filtered down to whichever
    indices are embedded cover art specifically — never a subtitle-font
    attachment (many foreign-language releases rely on those to render
    styled subs correctly in players that support it; stripping them would
    be a real regression, not a cleanup). Two real shapes: a video stream
    flagged `disposition.attached_pic` — the standard "this is really just
    a picture" signal ffmpeg itself uses for cover art in mp3/mp4/mkv
    alike, confirmed live to be exactly what ffmpeg's own `-attach`
    produces for an image file even in an MKV container, not a distinct
    `attachment`-type stream — or, as a defensive fallback for a muxer
    that classifies things differently, a genuine `attachment`-type
    stream whose mimetype is an image *and* whose filename actually looks
    like cover art (cover/poster/folder/fanart/banner) rather than, say, a
    font. Returns an empty list — never raises — on any ffprobe failure
    or on a source with nothing to strip;
    either way the caller's answer is the same: fall back to a plain
    hardlink rather than block organizing the file over this."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                "stream=index,codec_type:stream_tags=mimetype,filename:stream_disposition=attached_pic",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=config.FFPROBE_TIMEOUT_SECONDS,
        )
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        logger.warning("ffprobe failed on %r (%s); skipping embedded-artwork check", str(source), exc)
        return []

    indices = []
    for stream in streams:
        disposition = stream.get("disposition", {})
        tags = stream.get("tags", {})
        if stream.get("codec_type") == "video" and disposition.get("attached_pic") == 1:
            indices.append(stream["index"])
        elif stream.get("codec_type") == "attachment":
            mimetype = str(tags.get("mimetype", "")).lower()
            filename = str(tags.get("filename", ""))
            if mimetype.startswith("image/") and _COVER_NAME_RE.search(filename):
                indices.append(stream["index"])
    return indices


class AudioTrack(NamedTuple):
    """One audio stream, as much of it as this question needs."""

    language: str | None
    is_default: bool
    channels: int | None = None
    codec: str | None = None
    bitrate: int | None = None
    title: str | None = None

    @property
    def quality(self) -> tuple:
        return audio_quality_key(
            channels=self.channels, codec=self.codec, bitrate=self.bitrate, title=self.title
        )


def audio_tracks(source: Path) -> list[AudioTrack]:
    """`source`'s audio streams in order, or an empty list if ffprobe
    can't say. Never raises: a file this can't read is a file the caller
    leaves alone, which is the same answer as a file with nothing to fix.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-select_streams", "a",
                "-show_entries",
                "stream=channels,codec_name,bit_rate:stream_tags=language,title:stream_disposition=default",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=config.FFPROBE_TIMEOUT_SECONDS,
        )
        streams = json.loads(result.stdout or "{}").get("streams", []) if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        logger.warning("ffprobe failed reading audio tracks of %r (%s); leaving them alone", str(source), exc)
        return []
    def _int(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return [
        AudioTrack(
            language=(stream.get("tags") or {}).get("language"),
            is_default=bool((stream.get("disposition") or {}).get("default")),
            channels=_int(stream.get("channels")),
            codec=stream.get("codec_name"),
            bitrate=_int(stream.get("bit_rate")),
            title=(stream.get("tags") or {}).get("title"),
        )
        for stream in streams
    ]


def preferred_audio_track(tracks: list[AudioTrack], preferred: str) -> int | None:
    """Which audio track should carry the default flag, as an index into
    `tracks`, or None when there is nothing worth doing.

    Plex's audio-language preference is per Plex account, so a release
    whose French track is flagged default plays in French for every
    viewer who hasn't gone and changed their own settings — which is
    every guest on the server. The flag lives in the file, so deciding it
    here fixes it for everyone at once and needs nothing explained.

    Where a release carries several tracks in that language — an Atmos
    mix and a plain one, or the feature and a commentary — the best is
    chosen rather than the first the muxer happened to write. See
    language.audio_quality_key; taking the first gets the stereo AC3 as
    often as the 7.1, and occasionally gets the commentary.

    None for: one track (nothing to choose between), no track in the
    preferred language, or that track already being the only default.
    Each means the caller should leave the file exactly as it is.

    The decision lives apart from either way of acting on it — the
    organiser remuxes new files with ffmpeg, the backfill edits existing
    ones in place with mkvpropedit — because the two agreeing is the
    whole point, and two copies of this would eventually not.
    """
    if len(tracks) < 2:
        return None
    candidates = [i for i, t in enumerate(tracks) if is_audio_language(t.language, preferred)]
    if not candidates:
        return None
    wanted = max(candidates, key=lambda i: tracks[i].quality)
    defaults = [t.is_default for t in tracks]
    if defaults[wanted] and sum(defaults) == 1:
        return None
    return wanted


def _audio_default_disposition(source: Path, preferred: str) -> list[str]:
    """ffmpeg arguments that make the `preferred`-language audio track
    the default one, or an empty list when there is nothing to do.

    Indices here are audio-relative, not the global stream index —
    `-disposition:a:1` is the second *audio* stream, which is what ffmpeg
    wants and what ffprobe's order gives us.
    """
    tracks = audio_tracks(source)
    wanted = preferred_audio_track(tracks, preferred)
    if wanted is None:
        return []
    args: list[str] = []
    for i in range(len(tracks)):
        args += [f"-disposition:a:{i}", "default" if i == wanted else "0"]
    return args


def _remux(
    source: Path, target: Path, strip_indices: list[int], extra_args: list[str] | None = None
) -> bool:
    """Re-muxes `source` into `target`, dropping the given stream indices
    (embedded cover art) and stream-copying every other stream byte-for-
    byte (`-c copy`, no re-encode/no quality loss — near-instant even on a
    huge file, since nothing is actually decoded). Written to a temp file
    in target's own directory first and only renamed into place once
    ffmpeg exits clean, so a failed or interrupted run never leaves a
    half-written file sitting at the real target path. Returns False —
    never raises — on any ffmpeg failure, the same "fall back to a plain
    hardlink rather than block organizing" philosophy as the probe step
    above."""
    # The extension has to be the real last suffix, not buried before a
    # generic ".tmp" — ffmpeg infers its output muxer from the filename
    # extension, and a name ending in plain ".tmp" makes it refuse to
    # start at all ("Unable to choose an output format"), confirmed live
    # by this file's own test suite.
    extra_args = extra_args or []
    tmp = target.with_name(f".{target.stem}.stripping.tmp{target.suffix}")
    cmd = ["ffmpeg", "-y", "-i", str(source), "-map", "0"]
    for index in strip_indices:
        cmd += ["-map", f"-0:{index}"]
    cmd += ["-c", "copy", "-map_metadata", "0", *extra_args, str(tmp)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.FFMPEG_STRIP_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg failed stripping embedded artwork from %r (%s)", str(source), exc)
        tmp.unlink(missing_ok=True)
        return False
    if result.returncode != 0:
        logger.warning("ffmpeg failed stripping embedded artwork from %r: %s", str(source), result.stderr[-2000:])
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, target)
    return True


def _link_or_copy(source: Path, target: Path, preferred_audio_language: str | None = None) -> Path:
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
    strip_indices = _embedded_artwork_stream_indices(source)
    disposition = _audio_default_disposition(source, preferred_audio_language) if preferred_audio_language else []
    extra_args = list(disposition)
    if rewrap:
        # MP4 keeps text subtitles as mov_text, which Matroska will not
        # take and which aborts the whole remux under -c copy.
        streams = _streams(source) or []
        if any(stream.get("codec_name") == _MOV_TEXT for stream in streams):
            extra_args += ["-c:s", "srt"]
    if (rewrap or strip_indices or disposition) and _remux(source, target, strip_indices, extra_args):
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
    target = build_episode_path(show_identity, season, episode, source_path.suffix, title)
    target.parent.mkdir(parents=True, exist_ok=True)
    return _link_or_copy(source_path, target, preferred_audio_language)


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


def _pack_entries(
    torrent_hash: str | None, qbt: QBTClient, release_name: str | None
) -> tuple[list[tuple[str, Path]], str]:
    """Every file in the pack as (name, absolute source path) pairs —
    from qBittorrent while it still holds the torrent, and from the disk
    once it doesn't.

    The fallback exists because the torrent is not reliably still there
    when this runs, or when it is re-run. qBittorrent can be set to drop
    a torrent shortly after it finishes seeding (ten minutes, on the
    deployment this was written for) while leaving the data in place,
    and an organize that fails for any reason parks its request on
    "downloaded, not filed" — a status the rest of the app treats as
    recoverable. It wasn't: the file list only ever came from the
    torrent, so once that went, every retry failed identically and the
    request was stranded for good, including ones a later fix would
    otherwise have cleared. The files are still on disk; read them from
    there.

    Returns the entries and a short description of where they came
    from, so the caller's errors can say which."""
    info = qbt.torrent_info(torrent_hash) if torrent_hash else None
    if info is not None:
        save_path = info.get("save_path")
        if not save_path:
            raise MediaOrganizerError(f"torrent {torrent_hash!r} has no save_path")
        base = translate_qbit_save_path(save_path, config.QBIT_TV_SAVE_PATH, config.TV_LIBRARY_ROOT)
        files = qbt.torrent_files(torrent_hash)
        return [(f["name"], base / f["name"]) for f in files], f"torrent {torrent_hash!r}"

    if not release_name:
        raise MediaOrganizerError(
            f"torrent {torrent_hash!r} not found in qBittorrent, and no release name to find it on disk with"
        )
    release_dir = find_release_dir(config.TV_LIBRARY_ROOT, release_name)
    if release_dir is None:
        raise MediaOrganizerError(
            f"torrent {torrent_hash!r} not found in qBittorrent, and no folder matching "
            f"{release_name!r} under {config.TV_LIBRARY_ROOT}"
        )
    logger.info(
        "organize_pack: torrent %r is gone from qBittorrent — reading %s off disk instead",
        torrent_hash,
        release_dir,
    )
    entries = [
        (str(path.relative_to(release_dir.parent)), path)
        for path in sorted(release_dir.rglob("*"))
        if path.is_file()
    ]
    return entries, f"folder {str(release_dir)!r}"


def concat_parts(sources: list[Path], target: Path) -> bool:
    """Stitches a release's parts into the one episode TMDB says they
    are. Stream copy — nothing is re-encoded, and nothing is decoded.

    The parts come out of one release, so they share codec, resolution
    and channel layout, which is what makes `-c copy` safe here and
    would not make it safe in general.

    Verified by duration before anything replaces anything: concat can
    exit clean having written a short file if a part is unreadable, and
    the result of getting that wrong is an episode that looks filed and
    plays half of itself. A result more than five seconds short of the
    parts it came from is thrown away.

    Returns False rather than raising, so a pack whose parts won't
    stitch falls back to filing them as they are.
    """
    durations = [probe_duration_minutes(part) for part in sources]
    expected = sum(d for d in durations if d) if all(durations) else None

    listing = target.with_name(f".{target.stem}.concat.txt")
    tmp = target.with_name(f".{target.stem}.joining.tmp{target.suffix}")
    try:
        # ffmpeg's concat list quotes with single quotes and escapes them
        # the awkward way; a title with an apostrophe in it is not rare.
        listing.write_text(
            "".join(f"file '{str(part).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for part in sources),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-map", "0", "-map", "-0:d?", "-c", "copy", str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=config.FFMPEG_STRIP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not join %d part(s) into %r (%s)", len(sources), target.name, exc)
        listing.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        return False
    finally:
        listing.unlink(missing_ok=True)

    if result.returncode != 0:
        logger.warning("ffmpeg could not join %d part(s) into %r: %s",
                       len(sources), target.name, _ffmpeg_reason(result.stderr))
        tmp.unlink(missing_ok=True)
        return False

    joined = probe_duration_minutes(tmp)
    if expected and (joined is None or joined < expected - (5 / 60)):
        logger.warning(
            "joined %d part(s) into %r but it came out %.1f min against the %.1f min they run to — discarding it",
            len(sources), target.name, joined or 0, expected,
        )
        tmp.unlink(missing_ok=True)
        return False

    os.replace(tmp, target)
    logger.info("joined %d part(s) into %r (%.1f min)", len(sources), target.name, joined or 0)
    return True


def _pack_file_episode(name: str) -> tuple[int, int] | None:
    """Which (season, episode) one file in a pack is, or None when it is
    not an episode at all — not a video, a sample, or no episode token.

    The one reading of a pack's files, shared by pack_season_span (how
    many seasons does this pack hold?) and organize_pack (where does each
    file go?). They used to read names differently: the span looked at
    the whole path with the plain parser, the organizer at the file's own
    name with the "1x01" form allowed too. So a pack whose files the
    organizer could place, the span could miss — and a Justice League
    Unlimited pack whose first two seasons it didn't count looked like
    seasons 3-5 of one show, skipped the check that splits off the show
    bundled in front, and was filed as Unlimited from end to end
    (2026-09-29). One function, so the two can't disagree again."""
    path = Path(name)
    if path.suffix.lower() not in config.VIDEO_EXTENSIONS or has_token(path.stem, "sample"):
        return None
    return extract_episode_identity(tokenize(path.stem), allow_x_form=True)


def pack_season_span(
    torrent_hash: str | None, qbt: QBTClient, release_name: str | None = None
) -> tuple[int, int] | None:
    """The lowest and highest season numbered inside a pack, read from
    the files themselves rather than from the release's name.

    The name is a claim and the files are the fact, and for the decision
    this feeds — whether a pack is carrying more than one show — only
    the fact will do. Read exactly as organize_pack reads them (see
    _pack_file_episode). Season 0 is ignored: a bundled specials folder
    says nothing about how many series are in here.
    """
    entries, _ = _pack_entries(torrent_hash, qbt, release_name)
    seasons = set()
    for name, _path in entries:
        identity = _pack_file_episode(name)
        if identity and identity[0] > 0:
            seasons.add(identity[0])
    return (min(seasons), max(seasons)) if seasons else None


def organize_pack(
    show_identity: ShowIdentity,
    torrent_hash: str | None,
    qbt: QBTClient,
    release_name: str | None = None,
    place: Callable[[int, int], tuple[int, int, str | None]] | None = None,
    preferred_audio_language: str | None = None,
    only_seasons: tuple[int, int] | None = None,
    season_offset: int = 0,
    align: Callable[[int, list[int]], dict[int, list[int]] | None] | None = None,
) -> list[tuple[int, int, Path]]:
    """Stage 13: places every individually SxxEyy-identifiable file out of a
    completed season/complete-series pack torrent — an extension of
    `organize_episode`'s own per-file placement (`build_episode_path` +
    hardlink-or-copy), reused here unchanged for each file rather than
    reimplemented, per the plan's "reuse, don't duplicate" call. The only
    genuinely new logic is per-file: which (season, episode) a given file
    represents (`normalize.extract_episode_identity`, read from the file's
    own name, not from a single fixed request the way `organize_episode`
    is told its season/episode).

    A file with no recognizable episode token — a sample, .nfo, subtitle
    sidecar, or any other unmatched extra — is skipped and logged, never
    treated as an error: a pack's real content is "however many episodes
    it actually turns out to carry," not a fixed count this function
    checks against (Stage 13's "accept what's actually there" resolution
    of its own "partial pack" open decision). Raises `MediaOrganizerError`
    if neither the torrent nor, failing that, a folder on disk matching
    `release_name` can be found (see `_pack_entries`), or the more
    specific `NoVideoFileError`
    if literally nothing in it is recognizable as an episode — worker.py
    treats that specific case as grounds to purge the torrent outright,
    same as `select_video_file`'s own single-episode equivalent.

    Returns one `(season, episode, target_path)` tuple per file actually
    placed — the season and episode being where the file *went*, which
    `place` may have moved (a special appended to a season lands in
    Season 00), so the caller's ledger records where it is rather than
    what the release called it; the caller (worker.py) is what maps this back onto per-episode
    `requests` rows and Stage 12's `show_episodes` dedup ledger — this
    function only ever touches the filesystem, never the database."""
    entries, source_desc = _pack_entries(torrent_hash, qbt, release_name)
    placed: list[tuple[int, int, Path]] = []
    by_season: dict[int, list[tuple[int, Path, Path]]] = {}
    any_video_file = False
    for name, source_path in entries:
        path = Path(name)
        if path.suffix.lower() not in config.VIDEO_EXTENSIONS or has_token(path.stem, "sample"):
            continue
        any_video_file = True
        identity_pair = _pack_file_episode(name)
        if identity_pair is None:
            logger.info("organize_pack: skipping %r — no recognizable episode token", name)
            continue
        season, episode = identity_pair
        # A callable rather than prepared data: which seasons a pack
        # actually carries is only known here, file by file — a complete-
        # series pack would otherwise have to be pre-fetched season by
        # season on the chance it needed them. The caller memoises.
        #
        # It can move a file as well as name one. A pack that appends a
        # special to the end of a season (Invincible's Atom Eve ships as
        # S01E09, where TMDB has S00E01 and season 1 stops at 8) is
        # re-placed into Season 00 — see tv_resolve.episode_placement_lookup,
        # which owns that judgement and the TMDB data behind it.
        # A franchise pack holds more than one show: "Justice League
        # Unlimited S01-S05" is Justice League's two seasons followed by
        # Unlimited's three. The caller files it in two passes, each
        # claiming its own slice and renumbering it onto that show's own
        # seasons — see tv_resolve.find_predecessor_show.
        if only_seasons and not (only_seasons[0] <= season <= only_seasons[1]):
            continue
        season += season_offset
        by_season.setdefault(season, []).append((episode, path, source_path))

    # A release sometimes ships as several files what TMDB records as one
    # episode — Justice League's pilot is three broadcast parts against a
    # single 72-minute episode 1 — and every file after them is then
    # numbered ahead of the episode it claims to be. `align` says which
    # files make up which episode; see tv_resolve.align_pack_episodes,
    # which owns that judgement and refuses unless the arithmetic
    # balances.
    for season in sorted(by_season):
        files = sorted(by_season[season], key=lambda f: f[0])
        plan = align(season, [episode for episode, _p, _s in files]) if align else None
        if plan:
            by_number = {episode: entry for entry, episode in ((f, f[0]) for f in files)}
            grouped = [
                (target, [by_number[n] for n in sources if n in by_number])
                for target, sources in sorted(plan.items())
            ]
        else:
            grouped = [(entry[0], [entry]) for entry in files]
        for episode, members in grouped:
            if not members:
                continue
            _number, path, source_path = members[0]
            title: str | None = None
            if place:
                # The stem and a *lazy* duration: place() only reaches
                # for the runtime when the filename couldn't settle
                # which special a file is, so the extra ffprobe is paid
                # on the odd out-of-range file rather than on all twenty
                # in a pack.
                placed_season, episode, title = place(
                    season, episode, path.stem, lambda p=source_path: probe_duration_minutes(p)
                )
            else:
                placed_season = season
            target = build_episode_path(show_identity, placed_season, episode, source_path.suffix, title)
            target.parent.mkdir(parents=True, exist_ok=True)
            if len(members) > 1 and concat_parts([m[2] for m in members], target):
                placed.append((placed_season, episode, target))
                continue
            placed.append(
                (placed_season, episode, _link_or_copy(source_path, target, preferred_audio_language))
            )

    if not placed:
        if not any_video_file:
            raise NoVideoFileError(
                f"no video file at all among {len(entries)} file(s) in {source_desc}"
            )
        # Real video file(s) present, just none this app's naming parser
        # could pin to a season/episode — a genuine "can't file this yet"
        # case (unusual release naming), not a fake-release signal, so it
        # stays recoverable ("downloaded, not filed") rather than purged.
        raise MediaOrganizerError(
            f"no recognizable episode files found among {len(entries)} file(s) in {source_desc}"
        )
    return placed


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
    target = build_movie_path(identity, source_path.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    return _link_or_copy(source_path, target, preferred_audio_language)


# ---------------------------------------------------------------------------
# Backfill — the same decision applied to files already in the library.
#
# The organiser only ever sees a file on its way in, so every title filed
# before it learned this keeps whatever default its release shipped with.
# Audited 2026-09-27 against the live library: 126 of 1683 files carried
# an English track that wasn't the default, twenty of them one season of
# one show from an Italian release.
# ---------------------------------------------------------------------------

# mkvpropedit rewrites the header in place — milliseconds whatever the
# file's size, no copy, no re-mux. That is only safe away from the
# organiser: on the way in, the library path and qBittorrent's own copy
# are one inode, so editing the bytes corrupts a live torrent. Here the
# link count is checked first instead.
_MKVPROPEDIT = "mkvpropedit"


class AudioFixPlan(NamedTuple):
    path: Path
    tracks: list[AudioTrack]
    # Index into `tracks`, or None when the file is being skipped.
    wanted: int | None
    # None when there is something to do; otherwise why there isn't.
    skipped: str | None

    @property
    def summary(self) -> str:
        shown = " ".join(f"{t.language or 'none'}{'*' if t.is_default else ''}" for t in self.tracks)
        return f"[{shown}]"


def plan_audio_default_fix(root: Path, preferred: str) -> list[AudioFixPlan]:
    """What a backfill would change under `root`, and what it would not.

    Everything it declines to touch is reported rather than dropped: a
    silent skip in a job like this reads as "nothing was wrong", which is
    the one thing it must never be mistaken for.
    """
    plans: list[AudioFixPlan] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in (".mkv", ".mp4", ".m4v"):
            continue
        tracks = audio_tracks(path)
        wanted = preferred_audio_track(tracks, preferred)
        if wanted is None:
            continue
        if not is_filed(path):
            # Same reasoning as the container conversion: a release
            # folder under the library root is qBittorrent's, and
            # mkvpropedit rewrites the header in place.
            plans.append(AudioFixPlan(path, tracks, None, "not filed by Obsidian — a download, not a library copy"))
            continue
        if path.suffix.lower() != ".mkv":
            plans.append(AudioFixPlan(path, tracks, None, "not an MKV; mkvpropedit can't edit it"))
            continue
        try:
            links = path.stat().st_nlink
        except OSError as exc:
            plans.append(AudioFixPlan(path, tracks, None, f"can't stat it ({exc})"))
            continue
        if links > 1:
            # The organiser hardlinks, so more than one link means
            # qBittorrent is very likely still seeding these exact bytes.
            # Editing them in place would fail its next re-check.
            plans.append(AudioFixPlan(path, tracks, None, f"still hardlinked ({links} links) — probably seeding"))
            continue
        plans.append(AudioFixPlan(path, tracks, wanted, None))
    return plans


def apply_audio_default_fix(plan: AudioFixPlan) -> str | None:
    """Writes one plan's change. Returns None on success, or the reason
    it failed. mkvpropedit numbers tracks from 1 within their own type,
    so `track:a1` is the first audio track."""
    if plan.wanted is None:
        return "nothing to do"
    cmd = [_MKVPROPEDIT, str(plan.path)]
    for i in range(len(plan.tracks)):
        cmd += ["--edit", f"track:a{i + 1}", "--set", f"flag-default={1 if i == plan.wanted else 0}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.FFMPEG_STRIP_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return "mkvpropedit not installed (add mkvtoolnix to the backend image)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "mkvpropedit failed").strip()[-300:]
    return None


# ---------------------------------------------------------------------------
# MP4 -> MKV — a container change, never a re-encode.
#
# MKV is a superset of what MP4 can hold, so every video and audio stream
# is copied through byte for byte. Worth doing beyond tidiness: mkvpropedit
# can edit an MKV's header in place in milliseconds, which is what makes
# the audio-default backfill above cheap, and MP4 has no equivalent — the
# three MP4s in the live library were the only files that job had to skip.
# ---------------------------------------------------------------------------

# MP4 keeps text subtitles as mov_text, which Matroska will not take. They
# transcode to SRT on the way through — text to text, nothing lost but the
# positioning MP4 barely carried anyway. Image-based subtitles (PGS, VOBSUB)
# copy across untouched.
_MOV_TEXT = "mov_text"


class ContainerPlan(NamedTuple):
    source: Path
    target: Path
    # Stream counts to check the result against, so a truncated remux is
    # never mistaken for a finished one.
    video: int
    audio: int
    subtitles: int
    skipped: str | None

    @property
    def summary(self) -> str:
        return f"{self.video}v {self.audio}a {self.subtitles}s"


def _streams(source: Path) -> list[dict] | None:
    """Every stream ffprobe can see, or None if it can't read the file."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(source)],
            capture_output=True,
            text=True,
            timeout=config.FFPROBE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout or "{}").get("streams", [])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _probe_failure(source: Path) -> str:
    """Why ffprobe wouldn't read this file, in its own words.

    Worth the second probe on this path alone. "ffprobe can't read it"
    is a dead end; "moov atom not found" says the MP4 has no index and
    is not a file at all, which is a download to redo rather than a
    conversion to debug — and which Plex will fail on just as surely.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", str(source)],
            capture_output=True,
            text=True,
            timeout=config.FFPROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ffprobe failed ({exc})"
    lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
    for line in lines:
        if "moov atom not found" in line:
            return "no moov atom — the file has no index and will not play anywhere"
    detail = lines[-1] if lines else "no output"
    return f"ffprobe can't read it: {detail[-160:]}"


# The organiser's own folder marker. Every path it files carries one —
# build_movie_path and build_episode_path both append " {tmdb-<id>}" —
# so it is an exact test for "this app filed this", which is not the
# same question as "this is under the library root".
_FILED_FOLDER_RE = re.compile(r"\{tmdb-\d+\}")


def is_filed(path: Path) -> bool:
    """True when `path` sits in a folder the organiser created.

    qBittorrent downloads into the same dataset as the library — it has
    to, because the organiser hardlinks and a hardlink cannot cross
    filesystems — so a release's own folder sits right beside the filed
    copies, under the same root. Walking the root therefore finds both,
    and the difference matters enormously: a filed file is a hardlink
    whose twin qBittorrent still holds, so replacing it costs nothing,
    while a file in a release folder *is* the torrent's data.

    Confirmed live 2026-09-27 on a season being downloaded at the time:
    /tv-library/IT.Welcome.To.Derry.S01.COMPLETE...MP4-BEN.THE.MEN/ sat
    under the TV root with eight half-written episodes in it.
    """
    return any(_FILED_FOLDER_RE.search(part) for part in path.parent.parts)


def plan_container_conversion(root: Path) -> list[ContainerPlan]:
    """Which MP4s under `root` would become MKVs, and which wouldn't."""
    plans: list[ContainerPlan] = []
    for source in sorted(root.rglob("*")):
        if source.suffix.lower() not in (".mp4", ".m4v"):
            continue
        target = source.with_suffix(".mkv")
        if not is_filed(source):
            plans.append(
                ContainerPlan(source, target, 0, 0, 0, "not filed by Obsidian — a download, not a library copy")
            )
            continue
        streams = _streams(source)
        if streams is None:
            plans.append(ContainerPlan(source, target, 0, 0, 0, _probe_failure(source)))
            continue
        kinds = [s.get("codec_type") for s in streams]
        # Cover art rides as a video stream; it isn't part of the film and
        # the organiser already strips it from anything it files.
        real_video = sum(
            1
            for s in streams
            if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
        )
        plan = ContainerPlan(
            source, target, real_video, kinds.count("audio"), kinds.count("subtitle"), None
        )
        if target.exists():
            plans.append(plan._replace(skipped="an .mkv of the same name is already there"))
            continue
        if not real_video or not plan.audio:
            plans.append(plan._replace(skipped="no video or no audio stream"))
            continue
        plans.append(plan)
    return plans


def _ffmpeg_reason(stderr: str | None) -> str:
    """The line of ffmpeg's output that actually says what went wrong.

    A blind tail of the last 300 characters gave "eturn code -22" — the
    word `return` cut in half — while the line naming the real cause
    ("Only audio, video, and subtitles are supported for Matroska") sat
    just above the progress spam and was never shown.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for needle in ("are supported for Matroska", "Unsupported codec", "Could not write header",
                   "Invalid argument", "No space left", "Permission denied", "Error"):
        for line in reversed(lines):
            if needle in line:
                return line[-200:]
    return (lines[-1][-200:] if lines else "ffmpeg failed with no output")


def apply_container_conversion(plan: ContainerPlan) -> str | None:
    """Remuxes one MP4 into an MKV beside it and removes the MP4.

    Written to a temp name first and only moved into place once ffmpeg
    exits clean *and* the result carries the streams it should — a
    half-written file that merely exists is the one outcome worth
    guarding against here, because the step after it deletes the
    original.

    Safe on a hardlinked file, unlike the in-place audio edit: this never
    touches the source's bytes, and unlinking one of two hardlinks leaves
    the torrent's own copy whole.
    """
    if plan.skipped is not None:
        return plan.skipped
    streams = _streams(plan.source) or []
    subtitle_codec = "srt" if any(s.get("codec_name") == _MOV_TEXT for s in streams) else "copy"
    tmp = plan.target.with_name(f".{plan.target.stem}.remuxing.tmp.mkv")
    cmd = [
        "ffmpeg", "-y", "-i", str(plan.source),
        # Matroska takes video, audio, subtitles and attachments, and
        # nothing else. MP4 routinely carries a `bin_data` track —
        # QuickTime's old text/timecode shape, which ffmpeg cannot even
        # decode ("Unsupported codec with id 98314") — and mapping it
        # makes the muxer refuse to write a header at all, so the whole
        # remux fails having read nothing. Seen on every Doctor Who
        # episode and on each of the Ben The Men 2160p releases.
        #
        # Cover art goes by index, through the same helper the organiser
        # uses. The obvious "-0:v:m:attached_pic" does not work and does
        # not complain: `m:` matches a *metadata tag* of that name, not a
        # disposition, so it silently matched nothing and the art came
        # through. Found by checking the output rather than the exit code.
        "-map", "0", "-map", "-0:d?",
        *[arg for index in _embedded_artwork_stream_indices(plan.source) for arg in ("-map", f"-0:{index}")],
        "-c", "copy", "-c:s", subtitle_codec,
        "-map_metadata", "0",
        str(tmp),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.FFMPEG_STRIP_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        tmp.unlink(missing_ok=True)
        return str(exc)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        return _ffmpeg_reason(result.stderr)

    written = _streams(tmp)
    if written is None:
        tmp.unlink(missing_ok=True)
        return "the remuxed file came back unreadable"
    kinds = [s.get("codec_type") for s in written]
    if kinds.count("video") < plan.video or kinds.count("audio") != plan.audio:
        tmp.unlink(missing_ok=True)
        return (
            f"stream count changed ({plan.video}v {plan.audio}a in, "
            f"{kinds.count('video')}v {kinds.count('audio')}a out) — left alone"
        )

    os.replace(tmp, plan.target)
    plan.source.unlink(missing_ok=True)
    return None
