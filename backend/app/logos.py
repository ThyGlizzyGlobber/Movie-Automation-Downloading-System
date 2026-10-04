"""The content page's banner logo, re-encoded small and kept on disk.

The banner asked for TMDB's `original` logo, because w500 is too soft for
a mark drawn up to 720x240 CSS px on a retina screen. But `original` is
whatever the uploader sent: measured across ten real titles, 15KB to
4.7MB, 8.9MB in all, for an image that never needs more than 1440x480
device pixels. Fitted to that box and stored as WebP at quality 65 the
same ten come to 336KB, and side by side at full size on the app's dark
ground the two can't be told apart.

Most of what is left is the transparency, which WebP keeps lossless
unless told otherwise; only Pillow can tell it otherwise. Without Pillow
(an image built before it was added to requirements.txt) ffmpeg — already
in the image for the trailers — does the encoding with lossless alpha,
about 600KB for the same ten. If neither can, the caller is sent to the
original, so a logo is never lost to this.
"""

import concurrent.futures
import io
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import requests

from app import config
from app.cache import TTLCache

logger = logging.getLogger("app.logos")

TMDB_ORIGINAL_URL = "https://image.tmdb.org/t/p/original/"
# Where the original is fetched from, in order: the frontend's own image
# cache first (nginx.conf's /img/, on this compose network), which holds
# the originals every browser fetched before this existed and is local,
# then TMDB itself (1.5-2s for a large one, measured).
ORIGINAL_SOURCES = (os.environ.get("LOGO_LOCAL_IMAGE_URL", "http://frontend/img/original/"), TMDB_ORIGINAL_URL)
# Twice the banner's largest logo (DetailShell.css: min(720px, 44%) wide,
# 240px tall), so a retina screen still gets one device pixel per pixel.
MAX_WIDTH, MAX_HEIGHT = 1440, 480
QUALITY = 65
ALPHA_QUALITY = 65
# How long a request waits for an encode before sending the browser to the
# original instead. The detail call starts the encode (warm()), so by the
# time the browser asks it is usually done; when it isn't, waiting still
# beats the fallback, which is the multi-megabyte original.
WAIT_SECONDS = 10.0

# A logo name as TMDB issues them. Only PNGs: an SVG logo is already small
# and is left as it is.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}\.png$")

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="logo")
_in_flight: dict[str, concurrent.futures.Future] = {}
_lock = threading.RLock()
# A logo that couldn't be encoded is not tried again on every view.
_failed = TTLCache(60 * 60, max_entries=2048)


def is_logo_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def cached_logo_path(name: str) -> Path:
    return config.LOGO_CACHE_DIR / f"{Path(name).stem}.webp"


def _encode_with_pillow(data: bytes) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    image.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)
    out = io.BytesIO()
    # method 4, not 6: 6 took over 5s on one real logo for 3% less.
    image.save(out, "WEBP", quality=QUALITY, alpha_quality=ALPHA_QUALITY, method=4)
    return out.getvalue()


def _encode_with_ffmpeg(data: bytes) -> bytes | None:
    with tempfile.TemporaryDirectory() as tmp:
        src, dest = Path(tmp) / "in.png", Path(tmp) / "out.webp"
        src.write_bytes(data)
        fit = (
            f"scale='min(iw,{MAX_WIDTH})':'min(ih,{MAX_HEIGHT})'"
            ":force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", fit, "-frames:v", "1",
               "-c:v", "libwebp", "-quality", str(QUALITY), "-pix_fmt", "yuva420p", str(dest)]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            logger.info("ffmpeg unavailable for logo encoding", exc_info=True)
            return None
        if result.returncode != 0 or not dest.is_file():
            logger.info("ffmpeg couldn't encode a logo: %s", result.stderr.decode(errors="replace")[-300:])
            return None
        return dest.read_bytes()


def _encode(data: bytes) -> bytes | None:
    try:
        encoded = _encode_with_pillow(data)
    except Exception:  # noqa: BLE001 — a file Pillow can't read may still be one ffmpeg can
        logger.info("Pillow couldn't encode a logo", exc_info=True)
        encoded = None
    return encoded if encoded is not None else _encode_with_ffmpeg(data)


def _fetch_original(name: str) -> bytes:
    last: Exception | None = None
    for base in ORIGINAL_SOURCES:
        try:
            upstream = requests.get(base + name, timeout=(2, 20))
            upstream.raise_for_status()
            return upstream.content
        except Exception as exc:  # noqa: BLE001 — try the next source
            last = exc
    raise last or RuntimeError("no logo source")


def _build(name: str) -> Path | None:
    dest = cached_logo_path(name)
    if dest.is_file():
        return dest
    try:
        encoded = _encode(_fetch_original(name))
    except Exception:  # noqa: BLE001 — logged and remembered; the caller falls back to the original
        logger.info("couldn't fetch or encode logo %s", name, exc_info=True)
        encoded = None
    if not encoded:
        _failed.set((name,), True)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".part")
    partial.write_bytes(encoded)
    os.replace(partial, dest)
    _enforce_retention()
    return dest


def _enforce_retention() -> None:
    files = sorted(config.LOGO_CACHE_DIR.glob("*.webp"), key=lambda p: p.stat().st_mtime)
    for old in files[: max(len(files) - config.LOGO_CACHE_MAX_FILES, 0)]:
        old.unlink(missing_ok=True)


def _submit(name: str) -> concurrent.futures.Future | None:
    if _failed.get((name,))[1]:
        return None
    with _lock:
        future = _in_flight.get(name)
        if future is None:
            future = _pool.submit(_build, name)
            _in_flight[name] = future
            future.add_done_callback(lambda _f: _forget(name))
    return future


def _forget(name: str) -> None:
    with _lock:
        _in_flight.pop(name, None)


def warm(logo_path: str | None) -> None:
    """Start encoding a title's logo, if it isn't on disk yet, without
    waiting. The detail call does this, so the encode runs while the
    browser is still drawing the page that will ask for it."""
    name = (logo_path or "").lstrip("/")
    if is_logo_name(name) and not cached_logo_path(name).is_file():
        _submit(name)


def get(name: str) -> Path | None:
    """The encoded logo, waiting up to WAIT_SECONDS for it; None when it
    can't be had in that time, and the caller should send the original."""
    dest = cached_logo_path(name)
    if dest.is_file():
        return dest
    future = _submit(name)
    if future is None:
        return None
    try:
        return future.result(timeout=WAIT_SECONDS)
    except concurrent.futures.TimeoutError:
        return None
