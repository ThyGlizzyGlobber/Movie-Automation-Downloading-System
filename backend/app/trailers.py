"""Self-hosted trailer cache for the home hero carousel — downloads a
trailer once via yt-dlp and serves the local file directly, rather than
embedding YouTube's own iframe. See api.py's get_movie_trailer/
get_tv_trailer for why: YouTube's embed chrome (loading/buffering UI, and
especially its Cards feature — an interactive overlay the uploader can
configure at any timestamp, with no embed parameter or API call to
suppress it) proved impossible to fully mask from inside the iframe
sandbox.
"""

import concurrent.futures
import logging
from pathlib import Path

import yt_dlp

from app import config

logger = logging.getLogger("app.trailers")


def cached_trailer_path(media_type: str, tmdb_id: int, key: str) -> Path:
    return config.TRAILER_CACHE_DIR / f"{media_type}-{tmdb_id}-{key}.mp4"


def ensure_downloaded(media_type: str, tmdb_id: int, key: str) -> Path | None:
    """Return the local file for this trailer, downloading it first if needed.

    Video and audio capped at 1080p/m4a are fetched as separate streams and
    merged into one mp4 by ffmpeg (installed in the Dockerfile for exactly
    this) — a progressive, already-merged format was tried first to avoid
    needing ffmpeg at all, but confirmed live (2026-09-09) that YouTube no
    longer serves one for most videos at a usable resolution, failing
    every real download outright with "Requested format is not
    available". The final `/best` fallback only matters for the rare
    video that *does* still have a single progressive format and nothing
    matching the split selectors above it.
    """
    dest = cached_trailer_path(media_type, tmdb_id, key)
    if dest.exists():
        return dest

    config.TRAILER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(dest),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": config.TRAILER_DOWNLOAD_TIMEOUT_SECONDS,
        "retries": 1,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={key}"])
    except Exception:
        logger.exception("trailer download failed for %s %s (key=%s)", media_type, tmdb_id, key)
        dest.unlink(missing_ok=True)
        return None

    if not dest.exists():
        return None

    enforce_cache_retention()
    return dest


def enforce_cache_retention() -> None:
    if not config.TRAILER_CACHE_DIR.is_dir():
        return
    files = sorted(config.TRAILER_CACHE_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    excess = len(files) - config.TRAILER_CACHE_MAX_FILES
    for f in files[: max(excess, 0)]:
        f.unlink(missing_ok=True)


def probe_duration(key: str) -> int | None:
    """The clip's length in seconds, without downloading it. None if
    YouTube won't say (age gate, region block, removed video), which the
    caller treats as "unmeasurable" rather than as zero."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={key}", download=False)
    except Exception:
        logger.info("trailer duration probe failed for key=%s", key)
        return None
    duration = (info or {}).get("duration")
    return int(duration) if duration else None


def pick_shortest_suitable(candidates: list[dict]) -> str | None:
    """The key of the shortest candidate that is still long enough to be
    a preview, measuring each one.

    "Shortest" alone picks a 5s sting; the type label alone picks a 98s
    trailer over a 61s teaser that says the same thing (see
    tmdb.trailer_candidates for the measurements behind both). So:
    everything at or above TRAILER_MIN_SECONDS, shortest first. A title
    whose clips are all below the floor keeps the longest of them —
    something is better than a still poster, and that is the case where
    a sting is all that exists.

    Unmeasurable candidates are not discarded, only deprioritised: if
    nothing at all can be measured this returns the first candidate,
    which is `trailer_candidates`' own best type guess and exactly what
    the hero used before any of this."""
    if not candidates:
        return None
    shortlist = candidates[: config.TRAILER_PROBE_LIMIT]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(shortlist)) as pool:
        durations = list(pool.map(lambda v: probe_duration(v["key"]), shortlist))

    measured = [(d, v["key"]) for v, d in zip(shortlist, durations) if d]
    if not measured:
        return shortlist[0]["key"]
    long_enough = [m for m in measured if m[0] >= config.TRAILER_MIN_SECONDS]
    chosen = min(long_enough)[1] if long_enough else max(measured)[1]
    logger.info(
        "trailer pick: %s from %s",
        chosen,
        ", ".join(f"{k}={d}s" for d, k in measured),
    )
    return chosen


def resolve(media_type: str, tmdb_id: int, candidates: list[dict]) -> Path | None:
    """The hero's entry point: the local file for whichever of this
    title's candidates should play, downloading it on the first ask.

    Checks the cache across every candidate before measuring anything,
    which is what keeps the probing to once per title — the second
    request finds the file the first one chose and never calls YouTube
    at all. Eviction simply costs one more round of that."""
    for video in candidates:
        cached = cached_trailer_path(media_type, tmdb_id, video["key"])
        if cached.exists():
            return cached
    key = pick_shortest_suitable(candidates)
    if key is None:
        return None
    return ensure_downloaded(media_type, tmdb_id, key)
