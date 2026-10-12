"""RequestStore's TV tables: standing show subscriptions (`shows`), who
follows each on which server (`show_followers`), and the per-episode
ledger that stops an episode being fetched twice (`show_episodes`)."""

import dataclasses

from app.db.common import NON_TERMINAL_STATUSES, now_iso
from app.db.rows import ShowEpisodeRow, ShowRow


class StoreShows:
    """Mixed into RequestStore (store.py). Relies on its `_conn` and `_lock`."""

    # -- show followers --

    def follow_show(self, show_id: int, server_id: int) -> None:
        """`server_id` follows the show: the show is checked for new
        episodes, and the server is asking for what is already on its way
        for it as well as everything it produces from here on."""
        now = now_iso()
        placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO show_followers (show_id, server_id, created_at) VALUES (?, ?, ?)",
                (show_id, server_id, now),
            )
            self._conn.execute("UPDATE shows SET status = 'watching' WHERE id = ?", (show_id,))
            self._conn.execute(
                "INSERT OR IGNORE INTO request_asks (request_id, server_id, created_at) "
                f"SELECT id, ?, ? FROM requests WHERE show_id = ? AND status IN ({placeholders})",
                (server_id, now, show_id, *NON_TERMINAL_STATUSES),
            )
            self._conn.commit()

    def unfollow_show(self, show_id: int, server_id: int) -> int:
        """Stops `server_id` following the show; how many servers still
        follow it. Its asks on the show's requests stay — history, the same
        as when the last follower goes and the subscription is deleted."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM show_followers WHERE show_id = ? AND server_id = ?", (show_id, server_id)
            )
            self._conn.commit()
            return self._conn.execute(
                "SELECT COUNT(*) FROM show_followers WHERE show_id = ?", (show_id,)
            ).fetchone()[0]

    def is_following(self, show_id: int, server_id: int) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM show_followers WHERE show_id = ? AND server_id = ?", (show_id, server_id)
            ).fetchone()
            is not None
        )

    def list_shows_for_server(self, server_id: int, status: str | None = None) -> list[ShowRow]:
        """The shows this server has anything to do with — those it
        follows, and those it has asked for some of — each as it stands
        for this server: "watching" only while this server follows it
        and it is still being checked, otherwise "paused"."""
        rows = self._conn.execute(
            "SELECT s.*, EXISTS (SELECT 1 FROM show_followers f WHERE f.show_id = s.id AND f.server_id = ?) AS followed "
            "FROM shows s WHERE EXISTS (SELECT 1 FROM show_followers f WHERE f.show_id = s.id AND f.server_id = ?) "
            "OR EXISTS (SELECT 1 FROM requests r JOIN request_asks a ON a.request_id = r.id "
            "WHERE r.show_id = s.id AND a.server_id = ?) ORDER BY s.created_at DESC",
            (server_id, server_id, server_id),
        ).fetchall()
        shows = [
            dataclasses.replace(
                ShowRow._from_row(r), status="watching" if r["followed"] and r["status"] == "watching" else "paused"
            )
            for r in rows
        ]
        return [s for s in shows if status is None or s.status == status]

    def show_for_server(self, show_id: int, server_id: int) -> ShowRow | None:
        return next((s for s in self.list_shows_for_server(server_id) if s.id == show_id), None)

    # -- shows (Stage 12 standing subscriptions) --

    def create_show(self, tmdb_id: int, title: str, status: str = "watching", poster_path: str | None = None) -> ShowRow:
        """`status` defaults to "watching" — a real, explicit subscribe.
        `POST /api/shows/bulk-download` (Stage 14.x) passes "paused" when
        it has to create a show row purely to anchor a one-off bulk
        download that was requested before ever subscribing — a show
        created that way must NOT start receiving the standing
        per-episode catch-up/recheck (`_check_all_watching_shows` only
        ever iterates `status == "watching"` rows), since the whole point
        was "just this one download," not "start tracking new episodes."""
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO shows (tmdb_id, title, status, poster_path, created_at, last_checked_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (tmdb_id, title, status, poster_path, now),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        return self.get_show(row_id)

    def get_show(self, show_id: int) -> ShowRow | None:
        row = self._conn.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
        return ShowRow._from_row(row) if row else None

    def get_show_by_tmdb_id(self, tmdb_id: int) -> ShowRow | None:
        row = self._conn.execute("SELECT * FROM shows WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
        return ShowRow._from_row(row) if row else None

    def list_shows(self, status: str | None = None) -> list[ShowRow]:
        if status:
            rows = self._conn.execute("SELECT * FROM shows WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM shows ORDER BY id DESC").fetchall()
        return [ShowRow._from_row(r) for r in rows]

    def update_show_status(self, show_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE shows SET status = ? WHERE id = ?", (status, show_id))
            self._conn.commit()

    def set_show_tmdb_status(self, show_id: int, tmdb_status: str | None) -> None:
        with self._lock:
            self._conn.execute("UPDATE shows SET tmdb_status = ? WHERE id = ?", (tmdb_status, show_id))
            self._conn.commit()

    def update_show_last_checked(self, show_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE shows SET last_checked_at = ? WHERE id = ?", (now_iso(), show_id))
            self._conn.commit()

    def delete_show(self, show_id: int) -> bool:
        """Unsubscribes — stops future checks. `show_episodes`/`requests`
        rows referencing this show's id are deliberately left alone: they're
        download history, not the subscription itself, per "hidden, never
        unrecoverable". Resubscribing later creates a new show row with a
        new id, so its dedup ledger starts fresh against the old rows — a
        known, accepted gap, same style as this project's other named-not-
        solved gaps (e.g. season packs, alternate episode-token formats)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM shows WHERE id = ?", (show_id,))
            self._conn.execute("DELETE FROM show_followers WHERE show_id = ?", (show_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # -- show_episodes (Stage 12 per-episode dedup ledger) --

    def has_show_episode(self, show_id: int, season_number: int, episode_number: int) -> bool:
        """Whether this episode is handled: in the ledger with a request
        that wasn't cancelled. A cancelled request is a person saying
        "not this one" — it must not keep the episode off the table for
        every later season, series or follow request (live 2026-09-17:
        cancelled per-episode rows for Ted made the season fallback skip
        both seasons as "already handled")."""
        row = self._conn.execute(
            "SELECT 1 FROM show_episodes e LEFT JOIN requests r ON r.id = e.request_id "
            "WHERE e.show_id = ? AND e.season_number = ? AND e.episode_number = ? "
            "AND COALESCE(r.status, '') != 'cancelled'",
            (show_id, season_number, episode_number),
        ).fetchone()
        return row is not None

    # A ledger slot whose request ended this way isn't getting the
    # episode anywhere: a fresh request (a season pack's fallback, a new
    # add) may take it over.
    _DEAD_END_STATUSES = ("cancelled", "no qualifying results", "insufficient free space", "failed")

    def has_live_show_episode(self, show_id: int, season_number: int, episode_number: int) -> bool:
        """Whether the episode is on the way or already landed: in the
        ledger with a request that is queued, searching, downloading,
        importing or complete. Unlike `has_show_episode`, a slot held by
        a request that failed doesn't count — the pack fallbacks use this
        so a season with failed single-episode attempts still gets its
        season pack (live 2026-09-17: Ted's seasons were skipped as
        "handled" by dead per-episode rows)."""
        row = self._conn.execute(
            "SELECT 1 FROM show_episodes e JOIN requests r ON r.id = e.request_id "
            "WHERE e.show_id = ? AND e.season_number = ? AND e.episode_number = ? "
            "AND r.status IN ('queued', 'searching', 'downloading', 'downloaded, not filed', 'complete')",
            (show_id, season_number, episode_number),
        ).fetchone()
        return row is not None

    def add_show_episode(self, show_id: int, season_number: int, episode_number: int, request_id: int) -> None:
        """Claims the episode's ledger slot for `request_id`. An existing
        claim stands, unless its request was cancelled or failed — then
        the new request takes the slot over.

        Stamps the claim with whether upgrade-rechecking is on right now,
        rather than taking it as an argument: every one of the five
        callers would have to look it up and agree, and one forgetting
        would quietly opt an episode out for good."""
        with self._lock:
            recheck_opted_in = self._recheck_enabled_now()
            self._conn.execute(
                "INSERT INTO show_episodes (show_id, season_number, episode_number, request_id, created_at, recheck_opted_in) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(show_id, season_number, episode_number) DO UPDATE SET "
                # A slot taken over by a fresh request is a fresh claim,
                # so it adopts the setting as it stands now rather than
                # keeping whatever the abandoned attempt was created
                # under.
                "request_id = excluded.request_id, created_at = excluded.created_at, "
                "recheck_opted_in = excluded.recheck_opted_in "
                "WHERE (SELECT status FROM requests WHERE id = show_episodes.request_id) "
                "IN ('cancelled', 'no qualifying results', 'insufficient free space', 'failed')",
                (show_id, season_number, episode_number, request_id, now_iso(), 1 if recheck_opted_in else 0),
            )
            self._conn.commit()

    def forget_show_episodes(self, show_id: int, season_number: int) -> int:
        """Drops one season's rows from the per-episode dedup ledger.

        For "this copy is broken": the files are gone, so the ledger
        saying they were handled is now a lie — and it is the thing that
        would stop the re-request from filing their replacements. Returns
        how many rows went, for the log."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM show_episodes WHERE show_id = ? AND season_number = ?",
                (show_id, season_number),
            )
            self._conn.commit()
            return cursor.rowcount

    def forget_show_episode(self, show_id: int, season_number: int, episode_number: int) -> int:
        """`forget_show_episodes` for one episode: its file is gone (a
        drive that failed), and the episodes beside it may not be."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM show_episodes WHERE show_id = ? AND season_number = ? AND episode_number = ?",
                (show_id, season_number, episode_number),
            )
            self._conn.commit()
            return cursor.rowcount

    def list_show_episodes(self, show_id: int | None = None) -> list[ShowEpisodeRow]:
        if show_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM show_episodes WHERE show_id = ? ORDER BY id ASC", (show_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM show_episodes ORDER BY id ASC").fetchall()
        return [ShowEpisodeRow._from_row(r) for r in rows]

    def record_episode_recheck(self, show_episode_id: int, new_request_id: int | None = None) -> None:
        """Bumps `recheck_count`/`last_rechecked_at` for one recheck attempt.
        `new_request_id` repoints the ledger at a new `requests` row when the
        recheck actually produced one (a retry that found something, or a
        quality-upgrade replacement) — left `None` when a recheck ran but
        found nothing new, so `request_id` keeps pointing at the prior
        attempt's audit trail."""
        now = now_iso()
        with self._lock:
            if new_request_id is not None:
                self._conn.execute(
                    "UPDATE show_episodes SET request_id = ?, recheck_count = recheck_count + 1, "
                    "last_rechecked_at = ? WHERE id = ?",
                    (new_request_id, now, show_episode_id),
                )
            else:
                self._conn.execute(
                    "UPDATE show_episodes SET recheck_count = recheck_count + 1, last_rechecked_at = ? WHERE id = ?",
                    (now, show_episode_id),
                )
            self._conn.commit()
