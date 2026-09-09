"""Self-hosted trailer cache for the home hero carousel — downloads a
trailer once via yt-dlp and serves the local file directly, rather than
embedding YouTube's own iframe. See api.py's get_movie_trailer/
get_tv_trailer for why: YouTube's embed chrome (loading/buffering UI, and
especially its Cards feature — an interactive overlay the uploader can
configure at any timestamp, with no embed parameter or API call to
suppress it) proved impossible to fully mask from inside the iframe
sandbox.
"""

import logging
from pathlib import Path

import yt_dlp

from app import config

logger = logging.getLogger("app.trailers")


def cached_trailer_path(media_type: str, tmdb_id: int, key: str) -> Path:
    return config.TRAILER_CACHE_DIR / f"{media_type}-{tmdb_id}-{key}.mp4"


def ensure_downloaded(media_type: str, tmdb_id: int, key: str) -> Path | None:
    """Return the local file for this trailer, downloading it first if needed.

    Progressive (pre-merged video+audio) format capped at 720p on purpose:
    the backend container has no ffmpeg, so a format that needs muxing
    separate video/audio streams would fail outright.
    """
    dest = cached_trailer_path(media_type, tmdb_id, key)
    if dest.exists():
        return dest

    config.TRAILER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": "best[ext=mp4][height<=720]/best[height<=720]",
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
