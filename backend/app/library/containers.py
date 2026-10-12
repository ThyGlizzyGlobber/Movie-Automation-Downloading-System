"""What's inside a video file, read with ffprobe, and rewrapping what Plex
can't play directly with ffmpeg: a file's streams and running time, cover
art stored as a video stream, and converting a filed title's container."""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from app.core import config

logger = logging.getLogger(__name__)


_COVER_NAME_RE = re.compile(r"\b(cover|poster|folder|fanart|banner)\b", re.IGNORECASE)


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


def embedded_artwork_stream_indices(source: Path) -> list[int]:
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


def remux(
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
MOV_TEXT = "mov_text"


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


def probe_streams(source: Path) -> list[dict] | None:
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
        streams = probe_streams(source)
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


def ffmpeg_reason(stderr: str | None) -> str:
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
    streams = probe_streams(plan.source) or []
    subtitle_codec = "srt" if any(s.get("codec_name") == MOV_TEXT for s in streams) else "copy"
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
        *[arg for index in embedded_artwork_stream_indices(plan.source) for arg in ("-map", f"-0:{index}")],
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
        return ffmpeg_reason(result.stderr)

    written = probe_streams(tmp)
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
