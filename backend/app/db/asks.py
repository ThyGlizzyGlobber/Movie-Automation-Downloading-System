"""RequestStore's `request_asks` table: which Plex servers want each
request. One request row is one download; each server that asked keeps
its own ask, so a server only ever sees its own requests."""

import dataclasses
import sqlite3

from app.db.common import NON_TERMINAL_STATUSES, now_iso
from app.db.rows import RequestRow


class StoreAsks:
    """Mixed into RequestStore (store.py). Relies on its `_conn` and `_lock`."""

    # -- request asks: which servers want each request --
    #
    # A request row is one download. The library mounts are shared, so a
    # title asked for from two servers is fetched once and filed once, and
    # each server that wants it holds an ask on that one row. A server sees
    # only rows it has an ask on, and sees them under the name of whoever
    # asked from that server — never someone asking from another one. Rows
    # a followed show produces are asked for by each follower, with nobody
    # named. Cancelling or clearing takes away one server's ask; the row
    # itself goes only once nobody is left asking for it.

    _SCOPED_REQUESTS = (
        "SELECT r.*, a.plex_user_id AS ask_plex_user_id, a.username AS ask_username "
        "FROM requests r JOIN request_asks a ON a.request_id = r.id AND a.server_id = ?"
    )

    @staticmethod
    def _scoped_row(row: sqlite3.Row) -> RequestRow:
        """The row as one server sees it: asked for by whoever asked from
        there."""
        return dataclasses.replace(
            RequestRow._from_row(row),
            requested_by_plex_id=row["ask_plex_user_id"],
            requested_by_username=row["ask_username"],
        )

    def _add_ask(self, request_id: int, server_id: int, plex_user_id: str | None = None, username: str | None = None) -> None:
        """Inside the caller's lock. The first person to ask from a server
        is the one named there; a follower's unnamed ask takes the name
        of anyone who then asks for it by hand."""
        self._conn.execute(
            "INSERT INTO request_asks (request_id, server_id, plex_user_id, username, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(request_id, server_id) DO UPDATE SET plex_user_id = excluded.plex_user_id, "
            "username = excluded.username WHERE request_asks.plex_user_id IS NULL AND excluded.plex_user_id IS NOT NULL",
            (request_id, server_id, plex_user_id, username, now_iso()),
        )

    def _add_tv_asks(
        self,
        request_id: int,
        show_id: int,
        server_id: int | None,
        plex_user_id: str | None,
        username: str | None,
        asks_from: int | None,
    ) -> None:
        """A new episode or pack row is wanted by whoever wanted the row it
        came from, by every server following the show, and by the server
        it was asked from."""
        now = now_iso()
        if asks_from is not None:
            self._conn.execute(
                "INSERT OR IGNORE INTO request_asks (request_id, server_id, plex_user_id, username, created_at) "
                "SELECT ?, server_id, plex_user_id, username, ? FROM request_asks WHERE request_id = ?",
                (request_id, now, asks_from),
            )
        self._conn.execute(
            "INSERT OR IGNORE INTO request_asks (request_id, server_id, created_at) "
            "SELECT ?, server_id, ? FROM show_followers WHERE show_id = ?",
            (request_id, now, show_id),
        )
        if server_id is not None:
            self._add_ask(request_id, server_id, plex_user_id, username)

    def add_request_ask(
        self, request_id: int, server_id: int, plex_user_id: str | None = None, username: str | None = None
    ) -> None:
        """Another server asking for a download already under way — it
        joins that one rather than starting a second."""
        with self._lock:
            self._add_ask(request_id, server_id, plex_user_id, username)
            self._conn.commit()

    def remove_request_ask(self, request_id: int, server_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM request_asks WHERE request_id = ? AND server_id = ?", (request_id, server_id)
            )
            self._conn.commit()

    def request_ask_servers(self, request_id: int) -> set[int]:
        rows = self._conn.execute("SELECT server_id FROM request_asks WHERE request_id = ?", (request_id,)).fetchall()
        return {r["server_id"] for r in rows}

    def list_requests_for_server(self, server_id: int, status: str | None = None) -> list[RequestRow]:
        if status:
            rows = self._conn.execute(
                f"{self._SCOPED_REQUESTS} WHERE r.status = ? ORDER BY r.id DESC", (server_id, status)
            ).fetchall()
        else:
            rows = self._conn.execute(f"{self._SCOPED_REQUESTS} ORDER BY r.id DESC", (server_id,)).fetchall()
        return [self._scoped_row(r) for r in rows]

    def get_request_for_server(self, request_id: int, server_id: int) -> RequestRow | None:
        """The row as `server_id` sees it, or None when that server has no
        ask on it — to that server, it doesn't exist."""
        row = self._conn.execute(f"{self._SCOPED_REQUESTS} WHERE r.id = ?", (server_id, request_id)).fetchone()
        return self._scoped_row(row) if row else None

    def visible_request_ids(self, server_id: int, request_ids) -> set[int]:
        """Which of these requests `server_id` has an ask on."""
        ids = list(set(request_ids))
        if not ids:
            return set()
        marks = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT request_id FROM request_asks WHERE server_id = ? AND request_id IN ({marks})", (server_id, *ids)
        ).fetchall()
        return {r["request_id"] for r in rows}

    def get_latest_request_for_show_for_server(self, show_id: int, server_id: int) -> RequestRow | None:
        """get_latest_request_for_show, among the rows this server sees."""
        row = self._conn.execute(
            f"{self._SCOPED_REQUESTS} WHERE r.show_id = ? ORDER BY r.id DESC LIMIT 1", (server_id, show_id)
        ).fetchone()
        return self._scoped_row(row) if row else None

    def find_live_request(
        self,
        tmdb_id: int,
        media_type: str,
        season_number: int | None = None,
        episode_number: int | None = None,
        season_range_end: int | None = None,
    ) -> RequestRow | None:
        """A download of exactly this already under way, whichever server
        asked for it — what a new ask joins instead of starting a second
        copy of the same title on the same disk."""
        placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
        row = self._conn.execute(
            f"SELECT * FROM requests WHERE tmdb_id = ? AND media_type = ? AND season_number IS ? "
            f"AND episode_number IS ? AND season_range_end IS ? AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (tmdb_id, media_type, season_number, episode_number, season_range_end, *NON_TERMINAL_STATUSES),
        ).fetchone()
        return RequestRow._from_row(row) if row else None

    def clear_settled_for_server(self, server_id: int) -> int:
        """"Clear finished" for one server: takes its asks off every settled
        row, then deletes the rows nobody is asking for any more. Another
        server's history is left exactly as it was. The same rows are off
        limits as for purge_requests_older_than — never a live one, never
        one still owing its source cleanup."""
        placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
        settled = (
            f"status NOT IN ({placeholders}) "
            "AND (source_cleanup_status IS NULL OR source_cleanup_status != 'pending')"
        )
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM request_asks WHERE server_id = ? AND request_id IN (SELECT id FROM requests WHERE {settled})",
                (server_id, *NON_TERMINAL_STATUSES),
            )
            cleared = cur.rowcount
            self._conn.execute(
                f"DELETE FROM requests WHERE {settled} AND id NOT IN (SELECT request_id FROM request_asks)",
                tuple(NON_TERMINAL_STATUSES),
            )
            self._conn.commit()
            return cleared
