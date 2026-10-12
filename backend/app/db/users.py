"""RequestStore's people: users, their sessions, and the sign-in audit log."""

from app.db.common import now_iso
from app.db.rows import SessionRow, UserRow


class StoreUsers:
    """Mixed into RequestStore (store.py). Relies on its `_conn` and `_lock`."""

    # -- users / sessions (frontend migration Part C2) --

    def upsert_user(
        self,
        plex_user_id: str,
        username: str | None,
        is_admin: bool,
        avatar_url: str | None = None,
        server_tokens: dict[int, str | None] | None = None,
    ) -> UserRow:
        """Called on every successful login — `is_admin` is re-derived
        fresh each time from a live Plex access check (see api/auth.py), so a
        change in server ownership is picked up on the next sign-in
        without any migration. First-time sign-in inserts a new row;
        every later one just updates username/is_admin/last_login_at,
        leaving has_seen_tutorial and first_seen_at untouched.

        A changed display name is also written back over that person's own
        past requests. `requests.requested_by_username` is a denormalized
        copy taken when the request was made, and identity there is carried
        by `requested_by_plex_id`, not by the text — so before this, a Plex
        rename left every older request labelled with the old name forever
        while new ones used the new one, and the same person showed up
        under two names depending on which screen you were looking at.

        Here rather than resolved at read time because this is the only
        moment the name can change at all: Plex only ever tells us someone's
        name via their own token (plex.py's get_account_identity), so there
        is no path by which `users.username` moves without passing through
        this function. Joining `users` on every request read would buy the
        same answer, per page load, forever, to catch an update that can
        only originate three lines above.

        What it costs is the historical record of what someone was called
        at the time — deliberate. This app's requests list exists to answer
        "who do I go and ask about this", which is a question about now.

        `server_tokens` is what a sign-in found: every server this person
        can reach, by plex_servers.id, with their own token for it. It
        replaces what was known before — a server they've since lost
        access to stops counting them — and a token Plex didn't hand back
        doesn't blank one already stored. None leaves memberships alone."""
        now = now_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT username FROM users WHERE plex_user_id = ?", (plex_user_id,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE users SET username = ?, is_admin = ?, last_login_at = ?, "
                    "avatar_url = COALESCE(?, avatar_url) WHERE plex_user_id = ?",
                    (username, int(is_admin), now, avatar_url, plex_user_id),
                )
                # Only on an actual change, and never blanking a stored
                # name with a NULL Plex didn't answer with.
                if username and username != existing["username"]:
                    self._conn.execute(
                        "UPDATE requests SET requested_by_username = ? WHERE requested_by_plex_id = ?",
                        (username, plex_user_id),
                    )
                    self._conn.execute(
                        "UPDATE request_asks SET username = ? WHERE plex_user_id = ?", (username, plex_user_id)
                    )
            else:
                self._conn.execute(
                    "INSERT INTO users (plex_user_id, username, is_admin, has_seen_tutorial, "
                    "first_seen_at, last_login_at, avatar_url) "
                    "VALUES (?, ?, ?, 0, ?, ?, ?)",
                    (plex_user_id, username, int(is_admin), now, now, avatar_url),
                )
            if server_tokens is not None:
                ids = list(server_tokens)
                marks = ", ".join("?" for _ in ids)
                self._conn.execute(
                    f"DELETE FROM user_servers WHERE plex_user_id = ? AND server_id NOT IN ({marks})"
                    if ids else "DELETE FROM user_servers WHERE plex_user_id = ?",
                    [plex_user_id, *ids],
                )
                for server_id, token in server_tokens.items():
                    self._conn.execute(
                        "INSERT INTO user_servers (plex_user_id, server_id, server_token) VALUES (?, ?, ?) "
                        "ON CONFLICT(plex_user_id, server_id) DO UPDATE SET "
                        "server_token = COALESCE(excluded.server_token, server_token)",
                        (plex_user_id, server_id, token),
                    )
            self._conn.commit()
        return self.get_user(plex_user_id)

    def get_user(self, plex_user_id: str) -> UserRow | None:
        row = self._conn.execute("SELECT * FROM users WHERE plex_user_id = ?", (plex_user_id,)).fetchone()
        return UserRow._from_row(row) if row else None

    def mark_tutorial_seen(self, plex_user_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET has_seen_tutorial = 1 WHERE plex_user_id = ?", (plex_user_id,)
            )
            self._conn.commit()

    def create_session(
        self,
        session_id: str,
        plex_user_id: str,
        username: str | None,
        is_admin: bool,
        expires_at: str,
        server_id: int | None = None,
    ) -> SessionRow:
        now = now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, plex_user_id, username, is_admin, created_at, expires_at, server_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, plex_user_id, username, int(is_admin), now, expires_at, server_id),
            )
            self._conn.commit()
        return self.get_session(session_id)

    def set_session_server(self, session_id: str, server_id: int) -> SessionRow | None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET server_id = ? WHERE id = ?", (server_id, session_id))
            self._conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRow | None:
        """None for a missing *or expired* session — an expired row is
        deleted on read rather than left for a separate sweep, same
        "no session ever silently keeps working past its own guarantee"
        principle the periodic re-validation loop (Part G4) extends to
        revoked Plex access."""
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        session = SessionRow._from_row(row)
        if session.expires_at < now_iso():
            self.delete_session(session_id)
            return None
        return session

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def delete_non_admin_sessions(self) -> int:
        """Called when the linked Plex server changes (switching servers
        in Settings, Part C3's `PUT /api/plex/server`) — every non-admin
        session's access grant was checked against the *old* server, so
        it must re-authenticate against the new one rather than silently
        keep working. The admin who just made the switch stays signed in."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE is_admin = 0")
            self._conn.commit()
            return cur.rowcount

    def delete_all_sessions(self) -> int:
        """Part G4's admin "revoke all sessions" action, for if a device
        is ever lost — literally everyone, the calling admin's own
        current session included (unlike delete_non_admin_sessions
        above): the next request from any device, including this one,
        gets a clean 401 and has to sign back in through Plex again."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions")
            self._conn.commit()
            return cur.rowcount

    def get_user_server_token(self, plex_user_id: str, server_id: int) -> str | None:
        """That user's own access token for one of the linked servers.

        Its own method rather than a field on UserRow so it cannot be
        serialised by accident: UserRow is what /api/admin/users returns,
        and anything on it is one `**row.__dict__` away from the wire.
        Callers here want it for exactly one thing — asking Plex a question
        *as that person* — and the answer, not the token, is what travels."""
        row = self._conn.execute(
            "SELECT server_token FROM user_servers WHERE plex_user_id = ? AND server_id = ?", (plex_user_id, server_id)
        ).fetchone()
        return row["server_token"] if row else None

    def list_users(self) -> list[UserRow]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY is_admin DESC, last_login_at DESC").fetchall()
        return [UserRow._from_row(r) for r in rows]

    def set_user_flags(
        self,
        plex_user_id: str,
        *,
        can_request: bool | None = None,
    ) -> UserRow | None:
        sets, values = [], []
        for col, val in (("can_request", can_request),):
            if val is not None:
                sets.append(f"{col} = ?")
                values.append(int(val))
        if sets:
            with self._lock:
                self._conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE plex_user_id = ?", [*values, plex_user_id])
                self._conn.commit()
        return self.get_user(plex_user_id)

    def delete_user(self, plex_user_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE plex_user_id = ?", (plex_user_id,))
            self._conn.execute("DELETE FROM user_servers WHERE plex_user_id = ?", (plex_user_id,))
            cur = self._conn.execute("DELETE FROM users WHERE plex_user_id = ?", (plex_user_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def record_auth_event(
        self,
        event_type: str,
        plex_user_id: str | None = None,
        username: str | None = None,
        ip_address: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO auth_events (event_type, plex_user_id, username, ip_address, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_type, plex_user_id, username, ip_address, detail, now_iso()),
            )
            self._conn.commit()

    def list_auth_events(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM auth_events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_auth_events(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM auth_events").fetchone()[0]
