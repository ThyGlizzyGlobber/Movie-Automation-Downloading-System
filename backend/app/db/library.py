"""RequestStore's ledger of what this app filed (`library_items`), durable
across history clears, and the torrents and releases a person rejected."""

import json
import logging
import os

from app.db.common import now_iso

logger = logging.getLogger(__name__)


class StoreLibrary:
    """Mixed into RequestStore (store.py). Relies on its `_conn` and `_lock`."""

    # -- rejected_torrents (Stage 15) --

    def add_rejected_torrent(self, tmdb_id: int, torrent_hash: str) -> None:
        """Blacklists `torrent_hash` against `tmdb_id` — a fresh search for
        the same movie/show excludes it going forward, same as an
        already-in-qBittorrent hash (see pipeline.py's `excluded_hashes`
        parameter). `INSERT OR IGNORE`: re-rejecting the same hash (e.g. a
        duplicate API call) is a harmless no-op, not an error."""
        now = now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO rejected_torrents (tmdb_id, torrent_hash, created_at) VALUES (?, ?, ?)",
                (tmdb_id, torrent_hash.lower(), now),
            )
            self._conn.commit()

    def get_rejected_torrent_hashes(self, tmdb_id: int) -> set[str]:
        rows = self._conn.execute(
            "SELECT torrent_hash FROM rejected_torrents WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchall()
        return {row["torrent_hash"] for row in rows}

    # -- library_items: what this app filed, durable across history clears --

    def _record_library_items(self, request_id: int, paths: list[str], result: dict) -> None:
        """Inside `mark_organized`'s lock: one ledger row per filed path,
        carrying the request's identity and the release it came from."""
        row = self._conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            return
        winner = result.get("winner") or {}
        now = now_iso()
        for path in paths:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            self._conn.execute(
                "INSERT INTO library_items (tmdb_id, media_type, season_number, episode_number, path, torrent_hash, "
                "release_name, size_bytes, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET tmdb_id = excluded.tmdb_id, torrent_hash = excluded.torrent_hash, "
                "release_name = excluded.release_name, size_bytes = excluded.size_bytes, request_id = excluded.request_id, "
                "created_at = excluded.created_at",
                (
                    row["tmdb_id"],
                    row["media_type"],
                    row["season_number"],
                    row["episode_number"],
                    path,
                    result.get("torrent_hash"),
                    winner.get("fileName"),
                    size,
                    request_id,
                    now,
                ),
            )

    def _backfill_library_items(self) -> None:
        """One-off on start: ledger rows for files filed before the ledger
        existed, from whatever complete request rows are still around."""
        rows = self._conn.execute(
            "SELECT id, result_json FROM requests WHERE status = 'complete' AND result_json LIKE '%organized_paths%'"
        ).fetchall()
        with self._lock:
            for row in rows:
                try:
                    result = json.loads(row["result_json"]) if row["result_json"] else {}
                except ValueError:
                    continue
                paths = [p for p in result.get("organized_paths") or [] if isinstance(p, str)]
                if paths:
                    self._record_library_items(row["id"], paths, result)
            self._conn.commit()

    # A pack that handed its work to seasons or episodes, in every wording
    # the worker has used for it. Matched on the message because, before
    # "split" existed, the message was the only place the outcome lived.
    _SPLIT_MESSAGES = (
        "% requested one at a time.",
        "% requested one by one.",
        "%every season is already requested or on Plex.",
        "%every season is already requested or in Plex.",
        "%every aired episode is already requested.",
    )

    def _backfill_split_packs(self) -> None:
        """One-off on start: packs recorded as "no qualifying results"
        for handing off to seasons or episodes, from before that outcome
        had its own status. Left alone they read "No match" over shows
        that are entirely on Plex — The Pitt's whole-series row, filed
        hours before the fix that stopped new ones (2026-10-05). A pack
        with nothing to hand off keeps its no-match: that one is true."""
        clause = " OR ".join("error_message LIKE ?" for _ in self._SPLIT_MESSAGES)
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE requests SET status = 'split' WHERE media_type = 'pack' "
                f"AND status = 'no qualifying results' AND ({clause})",
                self._SPLIT_MESSAGES,
            )
            self._conn.commit()
        if cursor.rowcount:
            logger.info("relabelled %d handed-off pack request(s) as split", cursor.rowcount)

    def all_library_paths(self) -> list[str]:
        """Every path this app has filed into the library, whatever it
        came from. Used to recognise a download folder's files as
        duplicates of something already kept."""
        rows = self._conn.execute("SELECT path FROM library_items").fetchall()
        return [row["path"] for row in rows]

    def library_paths_by_torrent_hash(self) -> dict[str, list[str]]:
        """Every filed path, grouped by the torrent it came from.

        The ledger is the only record that survives history being
        cleared, which is exactly the case that left source folders
        orphaned — the request row carrying `pending_cleanup_hashes` was
        deleted before its sweep ran. This is what lets a reconciler
        find those folders again afterwards."""
        rows = self._conn.execute(
            "SELECT torrent_hash, path FROM library_items WHERE torrent_hash IS NOT NULL AND torrent_hash != ''"
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["torrent_hash"].lower(), []).append(row["path"])
        return grouped

    def library_paths_by_release(self) -> dict[str, list[str]]:
        """Every filed path, grouped by the name of the release it came
        from — the indexer's name for it, which is also how a download
        folder is found again once qBittorrent has forgotten the torrent
        (see library/filing.py's find_release_dir)."""
        rows = self._conn.execute(
            "SELECT release_name, path FROM library_items WHERE release_name IS NOT NULL AND release_name != ''"
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["release_name"], []).append(row["path"])
        return grouped

    def get_library_items(self, tmdb_id: int, media_type: str | None = None) -> list[dict]:
        """The files this app filed for a title, newest first."""
        if media_type:
            rows = self._conn.execute(
                "SELECT * FROM library_items WHERE tmdb_id = ? AND media_type = ? ORDER BY id DESC", (tmdb_id, media_type)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM library_items WHERE tmdb_id = ? ORDER BY id DESC", (tmdb_id,)).fetchall()
        return [dict(r) for r in rows]

    def library_summary(self, tmdb_id: int, media_types: tuple[str, ...]) -> dict:
        """What this app has actually filed for one title, rolled up: how
        many files, how much disk they occupy between them, and the
        distinct releases they came from.

        Sizes are the real on-disk ones `_record_library_items` measured
        with `os.path.getsize`, not a torrent's advertised size, so a
        show's total is what it genuinely takes up across every season —
        which is the whole point of asking at the show level rather than
        reading one request's recorded winner the way the movie page
        does.

        Release names come back raw and distinct rather than as a parsed
        resolution: the detail pages already derive that label from a
        release name (DetailBits' `qualityFromName`), and one parser
        shared by the movie and show pages is worth more than a second
        one here that could quietly drift from it. The list is bounded by
        how many distinct releases a title was actually filed from — one
        per season pack, so typically a handful even for a long show."""
        placeholders = ",".join("?" for _ in media_types)
        params = (tmdb_id, *media_types)
        totals = self._conn.execute(
            f"SELECT COUNT(*) AS files, COALESCE(SUM(size_bytes), 0) AS total_bytes, "
            f"MAX(created_at) AS added_at "
            f"FROM library_items WHERE tmdb_id = ? AND media_type IN ({placeholders})",
            params,
        ).fetchone()
        releases = self._conn.execute(
            f"SELECT DISTINCT release_name FROM library_items "
            f"WHERE tmdb_id = ? AND media_type IN ({placeholders}) "
            f"AND release_name IS NOT NULL AND release_name != '' ORDER BY release_name",
            params,
        ).fetchall()
        return {
            "files": totals["files"],
            "total_bytes": totals["total_bytes"],
            # When the newest file was filed. Newest rather than oldest so
            # a re-download or an upgraded season reads as "added" when it
            # actually landed, and so this survives the request row the
            # movie page used to date itself from.
            "added_at": totals["added_at"],
            "releases": [r["release_name"] for r in releases],
        }

    def remove_library_item(self, path: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM library_items WHERE path = ?", (path,))
            self._conn.commit()

    def library_items_under(self, folder: str) -> list[dict]:
        """Every file this app filed somewhere under `folder` — on a box,
        everything that was on one drive (app/box/drive_recovery.py)."""
        prefix = folder.rstrip("/") + "/"
        rows = self._conn.execute(
            "SELECT * FROM library_items WHERE substr(path, 1, ?) = ? ORDER BY id", (len(prefix), prefix)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_rejected_release(self, tmdb_id: int, name: str, size_bytes: int | None = None) -> None:
        """Blacklists a copy this app didn't add itself (so there's no
        torrent hash on record): the file's name as Plex held it and its
        exact size. The size is the real fingerprint — a filed copy has
        usually been renamed to "Title (Year)", which names nothing — and
        the name only counts when it still carries release metadata
        (see pipeline._is_rejected_release)."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO rejected_releases (tmdb_id, name, size_bytes, created_at) VALUES (?, ?, ?, ?)",
                (tmdb_id, name, size_bytes, now_iso()),
            )
            self._conn.commit()

    def get_rejected_releases(self, tmdb_id: int) -> list[dict]:
        rows = self._conn.execute("SELECT name, size_bytes FROM rejected_releases WHERE tmdb_id = ?", (tmdb_id,)).fetchall()
        return [{"name": row["name"], "size_bytes": row["size_bytes"]} for row in rows]
