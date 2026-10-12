"""RequestStore's `plex_servers` table: every server the admin linked,
their order and labels, and who belongs to which."""

from app.db.common import now_iso
from app.db.rows import PlexServerRow


class StorePlexServers:
    """Mixed into RequestStore (store.py). Relies on its `_conn` and `_lock`."""

    # -- Plex servers --

    def list_plex_servers(self) -> list[PlexServerRow]:
        rows = self._conn.execute("SELECT * FROM plex_servers ORDER BY priority, id").fetchall()
        return [PlexServerRow._from_row(r) for r in rows]

    def primary_plex_server(self) -> PlexServerRow | None:
        """The first server by priority: the one the worker asks, and the
        one anyone shared on more than one server lands on. None until
        setup has linked one — which is also what "setup complete" means."""
        row = self._conn.execute("SELECT * FROM plex_servers ORDER BY priority, id LIMIT 1").fetchone()
        return PlexServerRow._from_row(row) if row else None

    def get_plex_server(self, server_id: int) -> PlexServerRow | None:
        row = self._conn.execute("SELECT * FROM plex_servers WHERE id = ?", (server_id,)).fetchone()
        return PlexServerRow._from_row(row) if row else None

    def save_plex_server(self, machine_id: str, name: str | None, url: str, token: str) -> PlexServerRow:
        """Adds a server, or refreshes one already here with fresh
        connection details (a server's URL and token can both change).
        A refresh keeps its label and place in the order; a new server
        goes last."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO plex_servers (machine_id, name, url, token, priority, created_at) "
                "VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(priority) + 1, 0) FROM plex_servers), ?) "
                "ON CONFLICT(machine_id) DO UPDATE SET name = excluded.name, url = excluded.url, token = excluded.token",
                (machine_id, name, url, token, now_iso()),
            )
            row = self._conn.execute("SELECT * FROM plex_servers WHERE machine_id = ?", (machine_id,)).fetchone()
            if self._conn.execute("SELECT COUNT(*) FROM plex_servers").fetchone()[0] == 1:
                self._adopt_unlinked_history(row["id"])
            self._conn.commit()
        return PlexServerRow._from_row(row)

    def replace_plex_servers(self, machine_id: str, name: str | None, url: str, token: str) -> PlexServerRow:
        """Makes this the only server, carrying every other server's asks
        and follows over to it — switching servers moves the household,
        it doesn't start it again. Everyone else signed in through the
        servers going is signed out, as remove_plex_servers does."""
        with self._lock:
            server = self.save_plex_server(machine_id, name, url, token)
            for table in ("request_asks", "show_followers"):
                self._conn.execute(f"UPDATE OR IGNORE {table} SET server_id = ? WHERE server_id != ?", (server.id, server.id))
                self._conn.execute(f"DELETE FROM {table} WHERE server_id != ?", (server.id,))
            self.remove_plex_servers(keep=(machine_id,))
        return self.get_plex_server(server.id)

    def _adopt_unlinked_history(self, server_id: int) -> None:
        """The only server linked takes over the asks and follows left
        behind when Plex was unlinked (see remove_plex_servers)."""
        for table in ("request_asks", "show_followers"):
            self._conn.execute(
                f"UPDATE OR IGNORE {table} SET server_id = ? WHERE server_id NOT IN (SELECT id FROM plex_servers)",
                (server_id,),
            )
            self._conn.execute(f"DELETE FROM {table} WHERE server_id NOT IN (SELECT id FROM plex_servers)")

    def remove_plex_servers(self, keep: tuple[str, ...] = ()) -> int:
        """Removes every server whose machine id isn't in `keep` — all of
        them when unlinking the account. Whoever was signed in through a
        removed server is signed out, since the server their access was
        checked against is gone; an admin's session moves back to the
        primary instead."""
        with self._lock:
            marks = ", ".join("?" for _ in keep)
            where = f"machine_id NOT IN ({marks})" if keep else "1"
            gone = [r["id"] for r in self._conn.execute(f"SELECT id FROM plex_servers WHERE {where}", tuple(keep))]
            if gone:
                ids = ", ".join("?" for _ in gone)
                self._conn.execute(f"DELETE FROM sessions WHERE is_admin = 0 AND server_id IN ({ids})", gone)
                self._conn.execute(f"UPDATE sessions SET server_id = NULL WHERE server_id IN ({ids})", gone)
                self._conn.execute(f"DELETE FROM user_servers WHERE server_id IN ({ids})", gone)
                if keep:
                    # One server going while others stay: what it asked
                    # for and followed goes with it, and a show nobody
                    # follows any more stops being checked. Unlinking
                    # everything keeps both instead, for the next server
                    # linked to take over (save_plex_server) — a re-link
                    # must not cost the household its history and shows.
                    self._conn.execute(f"DELETE FROM request_asks WHERE server_id IN ({ids})", gone)
                    self._conn.execute(f"DELETE FROM show_followers WHERE server_id IN ({ids})", gone)
                    self._conn.execute(
                        "UPDATE shows SET status = 'paused' WHERE status = 'watching' "
                        "AND id NOT IN (SELECT show_id FROM show_followers)"
                    )
                self._conn.execute(f"DELETE FROM plex_servers WHERE id IN ({ids})", gone)
            self._conn.commit()
            return len(gone)

    def set_plex_server_label(self, server_id: int, label: str | None) -> PlexServerRow | None:
        with self._lock:
            self._conn.execute("UPDATE plex_servers SET label = ? WHERE id = ?", (label, server_id))
            self._conn.commit()
        return self.get_plex_server(server_id)

    def reorder_plex_servers(self, server_ids: list[int]) -> list[PlexServerRow]:
        """Puts the servers in this order, first being the primary. The
        caller passes every server's id exactly once."""
        with self._lock:
            for priority, server_id in enumerate(server_ids):
                self._conn.execute("UPDATE plex_servers SET priority = ? WHERE id = ?", (priority, server_id))
            self._conn.commit()
        return self.list_plex_servers()

    def user_server_ids(self) -> dict[str, list[int]]:
        """Each person's servers as of their last sign-in, primary first."""
        rows = self._conn.execute(
            "SELECT u.plex_user_id, u.server_id FROM user_servers u JOIN plex_servers s ON s.id = u.server_id "
            "ORDER BY s.priority, s.id"
        ).fetchall()
        out: dict[str, list[int]] = {}
        for r in rows:
            out.setdefault(r["plex_user_id"], []).append(r["server_id"])
        return out

    def count_plex_server_members(self) -> dict[int, int]:
        rows = self._conn.execute("SELECT server_id, COUNT(*) AS n FROM user_servers GROUP BY server_id").fetchall()
        return {r["server_id"]: r["n"] for r in rows}
