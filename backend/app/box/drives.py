"""Where media lives.

On a box (truenas/obsidian-box.yaml), every hard drive is its own storage,
mounted at /media/<drive> in the backend, qBittorrent and Plex alike, and
laid out the same way:

    /media/drive1/downloads/<category>   where qBittorrent saves
    /media/drive1/Movies                 the drive's part of the library
    /media/drive1/TV Shows

A download is sent to the drive with the most free space, and filed on that
same drive — a hardlink can't cross drives, and filing within one is
instant and takes no extra space. Plex's Movies and TV Shows list every
drive's folder. A drive that dies takes only its own titles with it.

The NAS updater adds a drive (truenas/obsidian-updater.py) and leaves
`.obsidian-drive.json` at its top: that marker is how a folder under
MEDIA_DRIVES_ROOT counts as a drive at all, so a half-made one never does.

Anywhere else — MEDIA_DRIVES_ROOT unset, as on an install with its own
qBittorrent and one library — there is a single implicit drive: the
configured MOVIE_LIBRARY_ROOT and TV_LIBRARY_ROOT, with qBittorrent saving
wherever its own settings say. Everything here behaves exactly as before.
"""

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core import config

MEDIA_DRIVES_ROOT = os.environ.get("MEDIA_DRIVES_ROOT") or None
MARKER = ".obsidian-drive.json"
MOVIES, TV_SHOWS, DOWNLOADS = "Movies", "TV Shows", "downloads"


@dataclass(frozen=True)
class Drive:
    id: str  # "drive1"; "library" for the one implicit drive off a box
    label: str  # what a person sees: "Drive 1"
    path: Path | None  # the drive's top folder; None off a box
    movies: Path
    tv: Path
    downloads: Path | None  # None off a box: qBittorrent's own setting applies
    size_bytes: int | None = None  # the drive's size when it was added


def managed() -> bool:
    """Whether drives are this install's own (a box), rather than one
    library configured by hand."""
    return bool(MEDIA_DRIVES_ROOT)


def _read_marker(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / MARKER).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def all_drives() -> list[Drive]:
    """Every drive in order: drive1, drive2, … on a box (by number, so
    drive10 follows drive9); the one implicit drive anywhere else."""
    if not managed():
        return [Drive("library", "Library", None, config.MOVIE_LIBRARY_ROOT, config.TV_LIBRARY_ROOT, None)]
    root = Path(MEDIA_DRIVES_ROOT)
    found = []
    try:
        folders = [f for f in root.iterdir() if f.is_dir()]
    except OSError:
        return []
    for folder in folders:
        marker = _read_marker(folder)
        if marker is None:
            continue
        found.append(Drive(
            id=folder.name,
            label=str(marker.get("label") or folder.name),
            path=folder,
            movies=folder / MOVIES,
            tv=folder / TV_SHOWS,
            downloads=folder / DOWNLOADS,
            size_bytes=marker.get("size_bytes") if isinstance(marker.get("size_bytes"), int) else None,
        ))
    return sorted(found, key=lambda d: (len(d.id), d.id))


def library_roots(kind: str) -> list[Path]:
    """Every drive's Movies (kind "movie") or TV Shows (kind "tv") folder."""
    return [d.movies if kind == "movie" else d.tv for d in all_drives()]


def download_roots() -> list[Path]:
    """Where download folders sit, for the sweeps that clear finished ones
    (app/library/reconcile.py): each drive's downloads/<category> on a box; off a
    box, the library folders, where qBittorrent's categories save."""
    if not managed():
        return [config.MOVIE_LIBRARY_ROOT, config.TV_LIBRARY_ROOT]
    return [d.downloads / category for d in all_drives() for category in (config.CATEGORY, config.TV_CATEGORY)]


def drive_of(path: Path | str) -> Drive | None:
    """The drive a path is on, judged by where it sits under its top
    folder; off a box, the one implicit drive."""
    drives = all_drives()
    if not managed():
        return drives[0]
    path = Path(path)
    for drive in drives:
        if drive.path == path or drive.path in path.parents:
            return drive
    return None


def library_root(kind: str, near: Path | str | None = None) -> Path:
    """Where to file something of `kind`: the Movies or TV Shows folder on
    the same drive as `near` (the downloaded file), so filing is a
    hardlink. Off a box, or for a path on no drive, the configured root
    (on a box, the first drive's)."""
    drive = drive_of(near) if near is not None else None
    if drive is None:
        drives = all_drives()
        if not drives:
            raise NoDriveError("There's no drive to put it on. Add one in Settings › Storage.")
        drive = drives[0]
    return drive.movies if kind == "movie" else drive.tv


class NoDriveError(RuntimeError):
    """A box with no drive yet: nowhere to download to."""


def free_bytes(drive: Drive) -> int:
    try:
        return shutil.disk_usage(drive.path or drive.tv).free
    except OSError:
        return 0


def usage(drive: Drive) -> dict:
    """Total, used and free bytes of the drive's filesystem; zeros when it
    can't be read (a drive that has gone missing)."""
    try:
        total, used, free = shutil.disk_usage(drive.path or drive.tv)
    except OSError:
        return {"total": 0, "used": 0, "free": 0, "readable": False}
    return {"total": total, "used": used, "free": free, "readable": True}


def download_drive() -> Drive | None:
    """The drive a new download goes to: the one with the most free space.
    None on a box with no drive yet."""
    drives = all_drives()
    if not drives:
        return None
    return max(drives, key=free_bytes)


def save_path(drive: Drive, category: str) -> str | None:
    """Where qBittorrent should save a download of `category` on `drive`,
    or None to leave it to qBittorrent's own setting (off a box)."""
    if drive.downloads is None:
        return None
    return str(drive.downloads / category)


def ready() -> bool:
    """Whether there's anywhere to download to: always off a box; on a box,
    once it has a drive."""
    return not managed() or bool(all_drives())
