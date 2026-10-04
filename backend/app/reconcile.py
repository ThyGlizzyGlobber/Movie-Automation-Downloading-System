"""Finding source torrents whose cleanup was lost, after the fact.

`worker._attempt_source_cleanup` removes a torrent once its files are
safely in the library, but two bugs used to strand that work: it gave up
permanently the first time an organized copy looked unreadable, and
clearing request history deleted the very rows carrying what still
needed cleaning up. Both are fixed, and neither fix reaches backwards —
the rows are marked done, or gone.

This re-derives the same answer from the library ledger instead, which
survives both. It asks one question per torrent qBittorrent still holds:
did this app file this torrent's content into the library, and is that
filed copy still there? If so the original is redundant, on exactly the
grounds the sweep would have used.

Read-only unless asked to act, so the safe move is to look first.
"""

import logging
import shutil
from pathlib import Path

from app import config
from app.media_organizer import find_release_dir
from app.normalize import normalize_text

logger = logging.getLogger(__name__)


def find_redundant_sources(store, qbt) -> list[dict]:
    """Torrents that are safe to remove, newest qBittorrent order.

    Four things must all hold, and anything else is left alone:

    - the torrent is in the ledger, so this app filed it (a torrent
      added by hand is never touched — it has no ledger row);
    - it has finished, so an in-progress download is never removed;
    - every path filed from it still exists, the same all-or-nothing
      rule the sweep uses: one torrent produced all of them, so while
      any is missing the original may be the only copy of it left;
    - it is still present in qBittorrent at all.
    """
    filed = store.library_paths_by_torrent_hash()
    if not filed:
        return []

    redundant = []
    for torrent in qbt.list_torrents():
        torrent_hash = str(torrent.get("hash") or "").lower()
        paths = filed.get(torrent_hash)
        if not paths:
            continue
        if (torrent.get("progress") or 0) < 1:
            continue
        missing = [p for p in paths if not Path(p).exists()]
        if missing:
            logger.info(
                "reconcile: leaving torrent %s (%s) alone — %d filed copy/copies missing",
                torrent_hash,
                torrent.get("name"),
                len(missing),
            )
            continue
        redundant.append(
            {
                "hash": torrent_hash,
                "name": torrent.get("name"),
                "size_bytes": torrent.get("size") or torrent.get("total_size") or 0,
                "filed_paths": paths,
            }
        )
    return redundant


def remove_redundant_sources(store, qbt, apply: bool = False) -> list[dict]:
    """What `find_redundant_sources` found, deleted when `apply` — each
    entry gaining a `removed` flag and, when a delete fails, the `error`
    that stopped it, so a partial run reports honestly rather than
    raising halfway through and leaving the caller guessing."""
    found = find_redundant_sources(store, qbt)
    for item in found:
        if not apply:
            item["removed"] = False
            continue
        try:
            qbt.delete_torrent(item["hash"], delete_files=True)
            item["removed"] = True
            logger.info("reconcile: removed redundant source %s (%s)", item["hash"], item["name"])
        except Exception as exc:  # noqa: BLE001 — reported per item, never fatal
            item["removed"] = False
            item["error"] = str(exc)
            logger.exception("reconcile: couldn't remove %s (%s)", item["hash"], item["name"])
    return found


# ---------------------------------------------------------------------------
# The same job from the filesystem side, for downloads qBittorrent has
# already forgotten.
# ---------------------------------------------------------------------------


def _filed_index(store) -> tuple[dict[tuple[int, int], str], set[str]]:
    """`{(st_dev, st_ino): library path}` plus the set of library paths
    themselves.

    Identity, not resemblance: `organize_*` places a file with `os.link`,
    so a filed copy and the download it came from are the *same inode*.
    Two links to one inode is a fact the filesystem guarantees — deleting
    one provably cannot touch the bytes the other still points at. No
    name or size matching, which could only ever guess."""
    by_inode: dict[tuple[int, int], str] = {}
    paths: set[str] = set()
    for filed in store.all_library_paths():
        path = Path(filed)
        try:
            stat = path.stat()
        except OSError:
            continue  # filed copy already gone — nothing it could vouch for
        by_inode[(stat.st_dev, stat.st_ino)] = filed
        paths.add(str(path))
    return by_inode, paths


def _redundant_videos(directory: Path, by_inode, filed_paths) -> tuple[list[Path], list[Path], int]:
    """(redundant videos, videos that are not redundant, total bytes) for
    one directory tree. A video that *is* a filed path is counted as not
    redundant, which is what stops an organized library folder from ever
    looking like a leftover."""
    redundant: list[Path] = []
    kept: list[Path] = []
    total = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in config.VIDEO_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            kept.append(path)
            continue
        if str(path) in filed_paths:
            kept.append(path)
        elif stat.st_nlink > 1 and (stat.st_dev, stat.st_ino) in by_inode:
            redundant.append(path)
            total += stat.st_size
        else:
            kept.append(path)
    return redundant, kept, total


def find_orphaned_download_dirs(store, roots) -> list[dict]:
    """Directories under `roots` that hold nothing but already-filed
    video.

    For the folders qBittorrent no longer has a torrent for — it removed
    the entry on completion, so `find_redundant_sources` has nothing to
    match on and the folder just sits there. This works from the disk
    instead.

    A directory qualifies only when it contains at least one video and
    *every* video in it is a redundant link of something in the library.
    One unrecognised video and the whole directory is left alone: the
    non-video files beside it (subtitles, artwork, an nfo) go with the
    folder when it is removed, so the bar for removing it is that nothing
    irreplaceable is in there."""
    by_inode, filed_paths = _filed_index(store)
    if not by_inode:
        return []

    found: list[dict] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            logger.info("reconcile: skipping %s — not a directory this container can see", root)
            continue
        for child in sorted(root_path.iterdir()):
            if not child.is_dir():
                continue
            redundant, kept, total = _redundant_videos(child, by_inode, filed_paths)
            if not redundant or kept:
                continue
            found.append(
                {
                    "path": str(child),
                    "size_bytes": total,
                    "videos": [str(p) for p in redundant],
                    "filed_as": [by_inode[(p.stat().st_dev, p.stat().st_ino)] for p in redundant],
                }
            )
    return found


def remove_orphaned_download_dirs(store, roots, apply: bool = False) -> list[dict]:
    """What `find_orphaned_download_dirs` found, deleted when `apply`.
    Per-directory error reporting, same as the torrent-side version."""
    found = find_orphaned_download_dirs(store, roots)
    for item in found:
        if not apply:
            item["removed"] = False
            continue
        try:
            shutil.rmtree(item["path"])
            item["removed"] = True
            logger.info("reconcile: removed orphaned download folder %s", item["path"])
        except OSError as exc:
            item["removed"] = False
            item["error"] = str(exc)
            logger.exception("reconcile: couldn't remove %s", item["path"])
    return found


# ---------------------------------------------------------------------------
# Download folders whose files were rewritten on the way into the library.
# ---------------------------------------------------------------------------


def find_stranded_release_dirs(store, qbt, roots) -> list[dict]:
    """Download folders this app filed from, found by the release's own
    name, for the files the hardlink rule above can never recognise.

    Filing writes a new file rather than a link whenever it has to
    change something — the preferred-language audio made default,
    cover art stripped, an MP4 rewrapped — so the library copy is its
    own inode and the download no longer shares anything with it. The
    per-request cleanup still removes it through qBittorrent, but only
    while qBittorrent holds the torrent, and rewriting a 4K season takes
    long enough that a share-limit rule removes the torrent first (The
    Penguin: half an hour of remuxing, the torrent dropped ten minutes
    in). After that the folder was nobody's to remove.

    The link back is the ledger instead: it records the release each
    filed path came from, and that name finds the folder exactly as
    organizing's own disk fallback does. Removing it finishes what the
    per-request cleanup would have done — qBittorrent deletes a
    torrent's files whole, extras included — under the same gates:

    - every path filed from that release is still in place;
    - qBittorrent holds no torrent by that name, so a download in
      progress, or a fresh grab of the same release, is never touched;
    - the folder is not a library folder and holds no filed path.
    """
    releases = store.library_paths_by_release()
    if not releases:
        return []
    active = {normalize_text(str(t.get("name") or "")) for t in qbt.list_torrents()}
    filed = {str(Path(p)) for paths in releases.values() for p in paths}
    filed.update(store.all_library_paths())

    found: list[dict] = []
    seen: set[str] = set()
    for release_name, paths in sorted(releases.items()):
        if not all(Path(p).exists() for p in paths):
            continue
        for root in roots:
            root_path = Path(root)
            if not root_path.is_dir():
                continue
            folder = find_release_dir(root_path, release_name)
            if folder is None or str(folder) in seen:
                continue
            if "{tmdb-" in folder.name or normalize_text(folder.name) in active:
                continue
            videos, total, holds_filed = [], 0, False
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                if str(path) in filed:
                    holds_filed = True
                    break
                total += path.stat().st_size
                if path.suffix.lower() in config.VIDEO_EXTENSIONS:
                    videos.append(str(path))
            if holds_filed or not videos:
                continue
            seen.add(str(folder))
            found.append(
                {
                    "path": str(folder),
                    "release": release_name,
                    "size_bytes": total,
                    "videos": videos,
                    "filed_as": sorted(paths),
                }
            )
    return found


def remove_stranded_release_dirs(store, qbt, roots, apply: bool = False) -> list[dict]:
    """What `find_stranded_release_dirs` found, deleted when `apply`."""
    found = find_stranded_release_dirs(store, qbt, roots)
    for item in found:
        if not apply:
            item["removed"] = False
            continue
        try:
            shutil.rmtree(item["path"])
            item["removed"] = True
            logger.info("reconcile: removed stranded download folder %s", item["path"])
        except OSError as exc:
            item["removed"] = False
            item["error"] = str(exc)
            logger.exception("reconcile: couldn't remove %s", item["path"])
    return found
