"""RequestStore's `requests` table: creating each kind of request, reading
them back, moving them through their statuses, and the per-person title
views recommendations learn from."""

import json
from datetime import datetime, timedelta, timezone

from app.db.common import NON_TERMINAL_STATUSES, now_iso
from app.db.rows import RequestRow


class StoreRequests:
    """Mixed into RequestStore (store.py). Relies on its `_conn` and `_lock`."""

    # -- requests --

    def create_request(
        self,
        tmdb_id: int,
        title: str,
        release_year: int | None,
        query: str | None,
        min_resolution: str | None = None,
        requested_by_plex_id: str | None = None,
        requested_by_username: str | None = None,
        redownload_mode: str | None = None,
        poster_path: str | None = None,
        server_id: int | None = None,
    ) -> RequestRow:
        """`server_id` is the server it was asked from (see "request
        asks" below); None for a row no server asked for."""
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (query, tmdb_id, title, release_year, status, min_resolution, "
                "requested_by_plex_id, requested_by_username, redownload_mode, poster_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
                (
                    query,
                    tmdb_id,
                    title,
                    release_year,
                    min_resolution,
                    requested_by_plex_id,
                    requested_by_username,
                    redownload_mode,
                    poster_path,
                    now,
                    now,
                ),
            )
            row_id = cur.lastrowid
            if server_id is not None:
                self._add_ask(row_id, server_id, requested_by_plex_id, requested_by_username)
            self._conn.commit()
        return self.get_request(row_id)

    def _show_poster(self, show_id: int) -> str | None:
        row = self._conn.execute("SELECT poster_path FROM shows WHERE id = ?", (show_id,)).fetchone()
        return row["poster_path"] if row else None

    def create_episode_request(
        self,
        tmdb_id: int,
        show_id: int,
        title: str,
        season_number: int,
        episode_number: int,
        poster_path: str | None = None,
        min_resolution: str | None = None,
        requested_by_plex_id: str | None = None,
        requested_by_username: str | None = None,
        server_id: int | None = None,
        asks_from: int | None = None,
    ) -> RequestRow:
        """The Stage 12 equivalent of `create_request` for one episode of a
        subscribed show — same table, same statuses, same watcher, per the
        plan's "reuse, don't duplicate" call. `query` is always None: an
        episode request is never a free-text search, it's already fully
        identified by `show_id`/`season_number`/`episode_number`.

        Asked for by every server following the show, by `server_id` when
        someone asked for it there, and by every server that asked for
        request `asks_from` — the row it was split from or replaces."""
        poster_path = poster_path or self._show_poster(show_id)
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (query, tmdb_id, title, release_year, status, media_type, "
                "show_id, season_number, episode_number, poster_path, min_resolution, "
                "requested_by_plex_id, requested_by_username, created_at, updated_at) "
                "VALUES (NULL, ?, ?, NULL, 'queued', 'episode', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tmdb_id, title, show_id, season_number, episode_number, poster_path, min_resolution,
                    requested_by_plex_id, requested_by_username, now, now,
                ),
            )
            row_id = cur.lastrowid
            self._add_tv_asks(row_id, show_id, server_id, requested_by_plex_id, requested_by_username, asks_from)
            self._conn.commit()
        return self.get_request(row_id)

    def create_pack_request(
        self,
        tmdb_id: int,
        show_id: int,
        title: str,
        season_number: int | None,
        season_range_end: int | None = None,
        requested_by_plex_id: str | None = None,
        requested_by_username: str | None = None,
        min_resolution: str | None = None,
        redownload_mode: str | None = None,
        poster_path: str | None = None,
        server_id: int | None = None,
        asks_from: int | None = None,
    ) -> RequestRow:
        """Stage 13 (+ Stage 14.x's season-range scope): the tracking row
        for one bulk season/season-range/complete-series pack search+add
        attempt — reuses the `requests` table/statuses/watcher a third way
        (`media_type='pack'`), same "reuse, don't duplicate" call
        `create_episode_request` already made for Stage 12. `season_number`
        set with `season_range_end` left `None` means "season N" (Stage
        13's original shape); both set means "seasons `season_number`
        through `season_range_end` inclusive"; both `None` means "complete
        series" — no separate `scope` column, since all three are already
        distinguishable this way. `episode_number` is always NULL: a pack
        row is never about one specific episode, only once it's organized
        does each actual episode found inside it get its own normal
        episode row (see worker.py's `_organize_and_complete_pack`).

        Asked for the same way as `create_episode_request`."""
        poster_path = poster_path or self._show_poster(show_id)
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (query, tmdb_id, title, release_year, status, media_type, "
                "show_id, season_number, episode_number, season_range_end, "
                "requested_by_plex_id, requested_by_username, min_resolution, redownload_mode, poster_path, "
                "created_at, updated_at) "
                "VALUES (NULL, ?, ?, NULL, 'queued', 'pack', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tmdb_id,
                    title,
                    show_id,
                    season_number,
                    season_range_end,
                    requested_by_plex_id,
                    requested_by_username,
                    min_resolution,
                    redownload_mode,
                    poster_path,
                    now,
                    now,
                ),
            )
            row_id = cur.lastrowid
            self._add_tv_asks(row_id, show_id, server_id, requested_by_plex_id, requested_by_username, asks_from)
            self._conn.commit()
        return self.get_request(row_id)

    def update_download_progress(self, request_id: int, progress: float) -> None:
        """Persists qBittorrent's live progress fraction on every download-
        watcher poll — see RequestRow's own comment. Deliberately doesn't
        touch `updated_at`: that field means "the request's state last
        changed" (status/result), and a routine progress tick isn't a
        state change in that sense — bumping it here would make e.g. the
        retention sweep's age-based purge (which only ever targets
        terminal-status rows anyway) and any "last changed" display
        elsewhere read as constantly fresh for no meaningful reason."""
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET download_progress = ? WHERE id = ?", (progress, request_id)
            )
            self._conn.commit()

    def get_request(self, request_id: int) -> RequestRow | None:
        row = self._conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return RequestRow._from_row(row) if row else None

    def get_requests(self, request_ids) -> dict[int, RequestRow]:
        """`get_request` for many ids in one query, keyed by id — missing
        ids are simply absent."""
        ids = list(set(request_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(f"SELECT * FROM requests WHERE id IN ({placeholders})", ids).fetchall()
        return {r["id"]: RequestRow._from_row(r) for r in rows}

    def list_live_packs_for_show(self, show_id: int) -> list[RequestRow]:
        """This show's pack requests still in flight (any scope), newest
        first — what the show page and the scheduler check before treating
        an episode as unrequested."""
        placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
        rows = self._conn.execute(
            f"SELECT * FROM requests WHERE show_id = ? AND media_type = 'pack' AND status IN ({placeholders}) "
            "ORDER BY id DESC",
            (show_id, *NON_TERMINAL_STATUSES),
        ).fetchall()
        return [RequestRow._from_row(r) for r in rows]

    def get_latest_organized_request(self, tmdb_id: int, media_types: tuple[str, ...] = ("movie",)) -> RequestRow | None:
        """The most recent *complete*, genuinely-organized request for this
        title — i.e. one this app itself placed a file for and knows the
        exact path of (`organized_paths` on record), not merely one that
        was ever created. Frontend migration Part K2: backs both
        `on_plex_tracked` (can "Overwrite" even be offered at all — only
        ever against a file this app has a confirmed record of, never a
        guess from the same fuzzy title/year match `on_plex` itself is)
        and, when a redownload actually completes in 'overwrite' mode,
        finding exactly which prior file to supersede.

        `media_types` defaults to `("movie",)`; a TV caller passes
        `("episode", "pack")` — a show's organized history can be either,
        never a single fixed type the way a movie's always is."""
        placeholders = ",".join("?" for _ in media_types)
        rows = self._conn.execute(
            f"SELECT * FROM requests WHERE tmdb_id = ? AND media_type IN ({placeholders}) AND status = 'complete' "
            "ORDER BY id DESC",
            (tmdb_id, *media_types),
        ).fetchall()
        for row in rows:
            candidate = RequestRow._from_row(row)
            if candidate.result and candidate.result.get("organized_paths"):
                return candidate
        return None

    def get_latest_request_for_show(self, show_id: int) -> RequestRow | None:
        """Most recent episode/pack request row for a subscribed show —
        backs the Watching list's "latest episode status" (Stage 14)."""
        row = self._conn.execute(
            "SELECT * FROM requests WHERE show_id = ? ORDER BY id DESC LIMIT 1", (show_id,)
        ).fetchone()
        return RequestRow._from_row(row) if row else None

    def list_pack_requests_for_show(
        self, show_id: int, season_number: int | None, season_range_end: int | None = None
    ) -> list[RequestRow]:
        """Every 'pack' request ever made for this show at this *exact*
        scope — one season, a specific season range (`season_number` as
        its start, `season_range_end` as its end), or the complete series
        (both `None`) — newest first. A season-range attempt and a
        single-season attempt starting at the same season track
        completely independent histories, matched on both columns via
        `IS`, which is NULL-safe (`x IS NULL` is true when `x` is NULL,
        unlike `x = NULL`, which is never true in SQL). Used by worker.py's
        check_show() (Stage 14.x) to decide whether a new automatic pack
        attempt is warranted: none tried yet, one already succeeded or is
        still in flight (don't duplicate), or a prior attempt failed and
        the configured recheck cooldown/opt-in/max-attempts should gate a
        retry — the same question `cancel_queued_requests_for_show` (a
        manual bulk-download's own concern) never needed to ask, since
        that path always fires immediately regardless of history."""
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE show_id = ? AND media_type = 'pack' "
            "AND season_number IS ? AND season_range_end IS ? ORDER BY id DESC",
            (show_id, season_number, season_range_end),
        ).fetchall()
        return [RequestRow._from_row(r) for r in rows]

    def list_requests(self, status: str | None = None) -> list[RequestRow]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM requests WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
        return [RequestRow._from_row(r) for r in rows]

    def list_requests_for_user(self, plex_user_id: str, limit: int = 200) -> list[RequestRow]:
        """One person's own request history, newest first — a feeder for
        their recommendation rows (see taste.py).

        Bounded because it exists to describe someone's taste, and the
        hundredth-most-recent request has already decayed to almost nothing
        by the time it gets weighed. Reading their whole history to compute
        the same answer would just be slower."""
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE requested_by_plex_id = ? "
            "OR id IN (SELECT request_id FROM request_asks WHERE plex_user_id = ?) ORDER BY id DESC LIMIT ?",
            (plex_user_id, plex_user_id, limit),
        ).fetchall()
        return [RequestRow._from_row(r) for r in rows]

    def record_title_view(self, plex_user_id: str, media_type: str, tmdb_id: int, title: str) -> None:
        """Someone opened a title's page — a taste signal (taste.py), kept
        as one row per person per title with its latest visit."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO title_views (plex_user_id, media_type, tmdb_id, title, views, viewed_at) VALUES (?, ?, ?, ?, 1, ?) "
                "ON CONFLICT (plex_user_id, media_type, tmdb_id) DO UPDATE SET views = views + 1, viewed_at = excluded.viewed_at, title = excluded.title",
                (plex_user_id, media_type, int(tmdb_id), title, now_iso()),
            )
            self._conn.commit()

    def list_title_views_for_user(self, plex_user_id: str, limit: int = 200) -> list[dict]:
        """One person's opened titles, most recent first. Bounded for the
        same reason list_requests_for_user is: it feeds a decaying taste
        signal, and the long tail has decayed to nothing."""
        rows = self._conn.execute(
            "SELECT media_type, tmdb_id, title, views, viewed_at FROM title_views WHERE plex_user_id = ? ORDER BY viewed_at DESC LIMIT ?",
            (plex_user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_requests_page(self, limit: int, offset: int = 0) -> list[RequestRow]:
        """Frontend migration Part D — the Activity Dashboard's own paged
        view of the full requests history (every request, not just a
        status subset like `list_requests(status=...)` above)."""
        rows = self._conn.execute(
            "SELECT * FROM requests ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [RequestRow._from_row(r) for r in rows]

    def count_requests(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]

    def count_requests_with_status(self, status: str) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM requests WHERE status = ?", (status,)).fetchone()[0]

    def count_completed_since(self, since_iso: str) -> int:
        """Requests that reached 'complete' (organized into Plex) at or
        after `since_iso` — the Settings storage panel's "added today /
        this week" counters. `updated_at` is the last status transition,
        which for a complete row is the moment it was filed."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM requests WHERE status = 'complete' AND updated_at >= ?", (since_iso,)
        ).fetchone()[0]

    def get_requester_stats(self) -> list[dict]:
        """Per-user aggregate counts for the Activity Dashboard — total
        requests and requests made this calendar month, grouped by
        whoever made them. Rows with no requester (worker-created, e.g. a
        subscription's automatic catch-up) are excluded — there's no
        person to attribute those to. `username` is read from that
        person's own *most recent* row (a correlated subquery, not a bare
        GROUP BY column — SQLite's bare-column value in a GROUP BY isn't
        guaranteed to be any particular row's, and a display name can
        genuinely change between one request and the next since it's
        denormalized per-row, same as title/release_year)."""
        month_start = (
            datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        )
        rows = self._conn.execute(
            """
            SELECT
                r1.requested_by_plex_id AS plex_user_id,
                (
                    SELECT r2.requested_by_username FROM requests r2
                    WHERE r2.requested_by_plex_id = r1.requested_by_plex_id
                    ORDER BY r2.id DESC LIMIT 1
                ) AS username,
                COUNT(*) AS total_requests,
                SUM(CASE WHEN r1.created_at >= ? THEN 1 ELSE 0 END) AS requests_this_month
            FROM requests r1
            WHERE r1.requested_by_plex_id IS NOT NULL
            GROUP BY r1.requested_by_plex_id
            ORDER BY total_requests DESC
            """,
            (month_start,),
        ).fetchall()
        return [
            {
                "plex_user_id": r["plex_user_id"],
                "username": r["username"],
                "total_requests": r["total_requests"],
                "requests_this_month": r["requests_this_month"],
            }
            for r in rows
        ]

    def update_status(
        self,
        request_id: int,
        status: str,
        error_message: str | None = None,
        result: dict | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET status = ?, error_message = ?, "
                "result_json = COALESCE(?, result_json), updated_at = ? WHERE id = ?",
                (status, error_message, json.dumps(result) if result is not None else None, now_iso(), request_id),
            )
            self._conn.commit()

    def mark_organized(
        self,
        request_id: int,
        organized_paths: list[str],
        pending_cleanup_hashes: list[str],
        next_attempt_at: str,
        superseded_paths: list[str] | None = None,
    ) -> None:
        """Marks a request 'complete' with everything worker.py's durable
        `_watch_source_cleanup` sweep needs to eventually remove the now-
        redundant original torrent(s) — `organized_paths` (the file(s)
        actually placed, re-checked before any deletion) and
        `pending_cleanup_hashes` (normally just the one torrent that was
        organized, but an episode upgrade also folds in the superseded
        torrent it replaced) both persisted into the existing `result`
        JSON blob alongside whatever's already there (the winning
        candidate, score breakdown, etc.), not a separate table.

        `superseded_paths` (frontend migration Part K2) is different in
        kind from `pending_cleanup_hashes`: a redownload's "overwrite"
        mode deletes the *previously organized library file* from an
        earlier, already-complete request — not a torrent (which may
        have already been cleaned up entirely, long before this
        redownload ever happened) — so it's removed via a direct
        filesystem path, not `qbt.delete_torrent()`. Gated by the exact
        same safety check as the torrent cleanup: only ever acted on
        once _attempt_source_cleanup has confirmed *this* row's own new
        `organized_paths` are genuinely present on disk."""
        with self._lock:
            row = self._conn.execute("SELECT result_json FROM requests WHERE id = ?", (request_id,)).fetchone()
            existing = json.loads(row["result_json"]) if row and row["result_json"] else {}
            merged = {
                **existing,
                "organized_paths": organized_paths,
                "pending_cleanup_hashes": pending_cleanup_hashes,
            }
            if superseded_paths:
                merged["superseded_paths"] = superseded_paths
            self._record_library_items(request_id, organized_paths, merged)
            self._conn.execute(
                "UPDATE requests SET status = 'complete', error_message = NULL, result_json = ?, "
                "source_cleanup_status = 'pending', source_cleanup_next_attempt_at = ?, updated_at = ? "
                "WHERE id = ?",
                (json.dumps(merged), next_attempt_at, now_iso(), request_id),
            )
            self._conn.commit()

    def list_due_source_cleanups(self) -> list[RequestRow]:
        """Every request whose organize-time cleanup is still owed and due
        right now — restart-safe by construction, same as every other
        polling loop in this app: re-derived fresh from the database on
        every call rather than tracked only in memory."""
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE source_cleanup_status = 'pending' "
            "AND source_cleanup_next_attempt_at <= ? ORDER BY id ASC",
            (now_iso(),),
        ).fetchall()
        return [RequestRow._from_row(r) for r in rows]

    def mark_source_cleanup_done(self, request_id: int) -> None:
        """Cleanup either succeeded or was deliberately, permanently
        skipped (e.g. the organized copy has gone missing) — either way,
        `list_due_source_cleanups` must never surface this row again."""
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET source_cleanup_status = 'done' WHERE id = ?", (request_id,)
            )
            self._conn.commit()

    def defer_source_cleanup(
        self,
        request_id: int,
        remaining_hashes: list[str],
        next_attempt_at: str,
        remaining_superseded_paths: list[str] | None = None,
        verify_attempts: int | None = None,
    ) -> None:
        """A cleanup attempt failed (or only partially succeeded, e.g. the
        current torrent deleted but a superseded one didn't) — stays
        'pending' with the still-outstanding hashes (and, frontend
        migration Part K2, any still-outstanding superseded file paths)
        persisted and a later `next_attempt_at`, so the next sweep picks
        up exactly where this one left off."""
        with self._lock:
            row = self._conn.execute("SELECT result_json FROM requests WHERE id = ?", (request_id,)).fetchone()
            existing = json.loads(row["result_json"]) if row and row["result_json"] else {}
            merged = {**existing, "pending_cleanup_hashes": remaining_hashes}
            if remaining_superseded_paths is not None:
                merged["superseded_paths"] = remaining_superseded_paths
            if verify_attempts is not None:
                # How many sweeps have found an organized copy unreadable.
                # Carried on the row, not in memory, so the count survives
                # a restart the same way the rest of this does.
                merged["cleanup_verify_attempts"] = verify_attempts
            self._conn.execute(
                "UPDATE requests SET result_json = ?, source_cleanup_next_attempt_at = ? WHERE id = ?",
                (json.dumps(merged), next_attempt_at, request_id),
            )
            self._conn.commit()

    def queued_request_ids(self) -> list[int]:
        rows = self._conn.execute("SELECT id FROM requests WHERE status = 'queued' ORDER BY id ASC").fetchall()
        return [r["id"] for r in rows]

    def recover_interrupted(self) -> int:
        """Boot-time recovery sweep. Only 'searching' rows are force-failed:
        that work (pipeline run, nothing added to qBittorrent yet) is
        genuinely and completely lost on a crash/restart. 'downloading' rows
        are deliberately left alone — the torrent already exists in
        qBittorrent independent of this backend, so the download watcher
        just resumes polling it on its next cycle. Marking a real
        in-progress download 'failed' here would be a false negative, not a
        recovery — see project.md's Stage 3 decision log."""
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE requests SET status = 'failed', "
                "error_message = 'interrupted, please retry', updated_at = ? WHERE status = 'searching'",
                (now,),
            )
            self._conn.commit()
            return cur.rowcount

    def purge_requests_older_than(self, days: int) -> int:
        """Deletes terminal (non-active) requests created more than `days`
        ago. `days=0` deletes every terminal request regardless of age —
        used by the "Clear My Requests" button, which reuses this same
        safety-filtered query rather than a separate unrestricted DELETE.

        A row still owing its source cleanup is never deleted, however
        old or terminal it is. What needs cleaning up lives *on the
        request row* (`pending_cleanup_hashes`), so deleting one before
        its sweep runs doesn't cancel the cleanup — it loses it, leaving
        the original torrent and its folder on disk with nothing left to
        say they were ever owed. Clearing history had been quietly
        orphaning exactly the folders this app had just organized."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM requests WHERE created_at < ? AND status NOT IN ({placeholders}) "
                "AND (source_cleanup_status IS NULL OR source_cleanup_status != 'pending')",
                (cutoff, *NON_TERMINAL_STATUSES),
            )
            self._conn.execute("DELETE FROM request_asks WHERE request_id NOT IN (SELECT id FROM requests)")
            self._conn.commit()
            return cur.rowcount

    def cancel_queued_requests_for_show(self, show_id: int, season_number: int | None, superseded_by: int | None = None) -> int:
        """A new bulk season/series download (Stage 13) makes a show's own
        still-`queued` requests redundant wherever it actually covers them
        — called from `POST /api/shows/{id}/bulk-download` right before the
        new pack request is created, so the queue doesn't stay cluttered
        with per-episode catch-up requests the bulk download is about to
        superseded. `season_number=None` (series scope) cancels *every*
        queued request for this show, regardless of season — a complete
        series covers all of them. A season scope only cancels queued
        requests for that exact season (episode rows in it, or an
        already-queued pack for that same season) — a season-5 bulk
        request must never touch an unrelated season-2 catch-up request
        still sitting in the queue. Only `queued` rows are touched:
        anything already `searching`/`downloading`/`complete` represents
        real work already done or in flight, which a bulk action has no
        business silently cancelling.

        `superseded_by` is the request doing the covering: it is never
        cancelled itself, and every server that asked for a request it
        cancels is asking for it instead — so nobody's episode turns into
        a cancelled row with the download that replaced it out of sight."""
        now = now_iso()
        where = "show_id = ? AND status = 'queued' AND id IS NOT ?"
        params: tuple = (show_id, superseded_by)
        if season_number is not None:
            where += " AND season_number = ?"
            params += (season_number,)
        with self._lock:
            if superseded_by is not None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO request_asks (request_id, server_id, plex_user_id, username, created_at) "
                    "SELECT ?, server_id, plex_user_id, username, ? FROM request_asks "
                    f"WHERE request_id IN (SELECT id FROM requests WHERE {where})",
                    (superseded_by, now, *params),
                )
            cur = self._conn.execute(
                f"UPDATE requests SET status = 'cancelled', updated_at = ? WHERE {where}", (now, *params)
            )
            self._conn.commit()
            return cur.rowcount
