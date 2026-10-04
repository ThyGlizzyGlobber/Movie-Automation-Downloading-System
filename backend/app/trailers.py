"""Self-hosted trailer cache for the home hero carousel — downloads a
trailer once via yt-dlp and serves the local file directly, rather than
embedding YouTube's own iframe. See api/titles.py's get_movie_trailer/
get_tv_trailer for why: YouTube's embed chrome (loading/buffering UI, and
especially its Cards feature — an interactive overlay the uploader can
configure at any timestamp, with no embed parameter or API call to
suppress it) proved impossible to fully mask from inside the iframe
sandbox.
"""

import concurrent.futures
import logging
import os
import threading
from pathlib import Path

import yt_dlp

from app import config
from app.cache import TTLCache

logger = logging.getLogger("app.trailers")


def _mark_used(path: Path) -> Path:
    """Stamp a cache hit onto the file's mtime.

    Retention sorts by mtime, so what that timestamp records decides
    what the cache actually is. Set once at download and never touched,
    it made this a queue: a trailer rewatched every week was evicted on
    the same schedule as one seen once and forgotten, and then cost the
    content page another download to get back. Touched on every hit, the
    same sort becomes least-recently-used and the ones being watched
    stay.

    Best effort — a cache that cannot be reordered is still a cache, and
    a read-only moment here should never turn into a failed request."""
    try:
        os.utime(path, None)
    except OSError:
        logger.debug("couldn't mark trailer as used: %s", path, exc_info=True)
    return path


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
        return _mark_used(dest)

    config.TRAILER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        # H.264 video and AAC audio, explicitly, even though YouTube's
        # "best" at this resolution is usually VP9 and Opus and smaller
        # for it. VP9 in an .mp4 container is a combination Apple's
        # players do not accept — confirmed on a real iPhone and iPad,
        # both of which showed a still where Chrome played the trailer,
        # silently, because the container says mp4 and nothing errors.
        # The selector walks down rather than off a cliff: H.264 at
        # 1080p, else the best progressive mp4, else whatever exists, so
        # a video published only in VP9 still gets a file rather than
        # none. The cost is bytes — H.264 needs more of them for the
        # same picture — which is the trade for playing everywhere.
        "format": (
            "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            "best[height<=1080][ext=mp4]/"
            "best[height<=1080]/best"
        ),
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
    """Drop the least recently used files beyond the cap.

    Least recently *used*, not oldest downloaded — see _mark_used for why
    the distinction matters and what keeps the mtimes honest."""
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


def cached_clip(media_type: str, tmdb_id: int, candidates: list[dict]) -> Path | None:
    """Whichever of this title's candidates is already on disk, if any."""
    for video in candidates:
        cached = cached_trailer_path(media_type, tmdb_id, video["key"])
        if cached.exists():
            return _mark_used(cached)
    return None


def resolve(media_type: str, tmdb_id: int, candidates: list[dict]) -> Path | None:
    """The hero's entry point: the local file for whichever of this
    title's candidates should play, downloading it on the first ask.

    Checks the cache across every candidate before measuring anything,
    which is what keeps the probing to once per title — the second
    request finds the file the first one chose and never calls YouTube
    at all. Eviction simply costs one more round of that."""
    cached = cached_clip(media_type, tmdb_id, candidates)
    if cached is not None:
        return cached
    key = pick_shortest_suitable(candidates)
    if key is None:
        return None
    return ensure_downloaded(media_type, tmdb_id, key)


# Downloads happen here rather than inside the request that asked. Measuring
# a title's candidates and fetching the winner can take a minute (7s was
# typical, measured), and the hero, the content page and every viewer's
# browser all held a connection open for it — on the LAN, one of the six a
# browser allows per host, so posters queued behind a trailer. Two at a
# time: these share the NAS's line with everything else.
_download_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="trailer")
_in_flight: set[tuple[str, int]] = set()
_in_flight_lock = threading.Lock()
# A title whose download came to nothing (no usable clip, YouTube said no)
# isn't queued again on every page view; it gets another try after this.
_NO_TRAILER_RETRY_SECONDS = 60 * 60
_no_trailer = TTLCache(_NO_TRAILER_RETRY_SECONDS, max_entries=2048)


def _download_in_background(media_type: str, tmdb_id: int, candidates: list[dict]) -> None:
    key = (media_type, tmdb_id)
    try:
        if resolve(media_type, tmdb_id, candidates) is None:
            _no_trailer.set(key, True)
    except Exception:  # noqa: BLE001 — a failed download is a title without a trailer, not a crash
        logger.warning("background trailer download failed for %s %s", media_type, tmdb_id, exc_info=True)
        _no_trailer.set(key, True)
    finally:
        with _in_flight_lock:
            _in_flight.discard(key)


def resolve_without_waiting(media_type: str, tmdb_id: int, candidates: list[dict]) -> tuple[Path | None, bool]:
    """`(file, pending)`: the file when it is already on disk, otherwise
    None and whether a download is now under way — started here if it
    wasn't already — so the caller can come back for it."""
    if not candidates:
        return None, False
    cached = cached_clip(media_type, tmdb_id, candidates)
    if cached is not None:
        return cached, False
    key = (media_type, tmdb_id)
    if _no_trailer.get(key)[1]:
        return None, False
    with _in_flight_lock:
        if key in _in_flight:
            return None, True
        _in_flight.add(key)
    _download_pool.submit(_download_in_background, media_type, tmdb_id, candidates)
    return None, True
