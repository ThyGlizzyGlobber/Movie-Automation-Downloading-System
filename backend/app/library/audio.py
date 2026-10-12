"""Which audio track a filed title plays by default: its tracks, the one
the household would choose, and setting that as the default — as the file
is filed, or afterwards on one already in the library."""

import json
import logging
import subprocess
from pathlib import Path
from typing import NamedTuple

from app.catalogue.language import audio_quality_key, is_audio_language
from app.core import config
from app.library.containers import is_filed

logger = logging.getLogger(__name__)


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


def audio_default_disposition(source: Path, preferred: str) -> list[str]:
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
