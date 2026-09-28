"""SQLite job store — request state survives backend restarts. Plain
sqlite3, no ORM (household scale, per the plan's confirmed architecture).
A single connection with `check_same_thread=False` guarded by a
`threading.Lock`; callers on the async side wrap calls in
`asyncio.to_thread` so a query never blocks the event loop."""

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

# Callers import the row types from app.db rather than app.db_rows, so
# they stay importable from here.
from app.db_rows import RequestRow, SessionRow, ShowEpisodeRow, ShowRow, UserRow
from app.db_schema import StoreSchema

# Rows in these statuses are still live — an active job, or a torrent the
# download watcher is still tracking. Retention purges (automatic or the
# "Clear My Requests" button) never touch them, only settled history.
NON_TERMINAL_STATUSES = {"queued", "searching", "downloading"}

# Settled without a filed download, and not by choice — what the episode
# auto-recheck retries and /api/admin/jobs lists by default.
FAILURE_STATUSES = frozenset({"failed", "no qualifying results", "insufficient free space", "downloaded, not filed"})

# The same, plus a deliberate "cancelled": every way a request can end up
# settled without the episode, which a season view shows as failed.
FAILED_OR_CANCELLED_STATUSES = FAILURE_STATUSES | {"cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Rows:
    """A statement's result, already read off the cursor.

    The store shares one connection across threads, so a cursor must
    never outlive the lock that produced it: several threads stepping
    cursors on the same connection is exactly the `sqlite3.InterfaceError:
    bad parameter or other API misuse` that searching three requests at
    once surfaced (config.SEARCH_CONCURRENCY). Callers only ever want
    fetchone/fetchall/rowcount/lastrowid or to iterate, all of which this
    serves from memory."""

    __slots__ = ("_rows", "rowcount", "lastrowid")

    def __init__(self, rows, rowcount, lastrowid):
        self._rows = rows
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _SerializedConnection:
    """The one sqlite3 connection, with every statement run to completion
    under the store's lock.

    Only writes used to take that lock, which was survivable while a
    single task touched the store at a time. It isn't once several do —
    and the watcher loops were already concurrent with the queue, so this
    was a narrow race before it became a reliable one."""

    def __init__(self, conn: sqlite3.Connection, lock):
        self._conn = conn
        self._lock = lock

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            # description is None for anything that isn't a SELECT.
            return _Rows(cur.fetchall() if cur.description else [], cur.rowcount, cur.lastrowid)

    def executemany(self, sql, seq_of_params):
        with self._lock:
            cur = self._conn.executemany(sql, seq_of_params)
            return _Rows([], cur.rowcount, cur.lastrowid)

    def executescript(self, script):
        with self._lock:
            cur = self._conn.executescript(script)
            return _Rows([], cur.rowcount, cur.lastrowid)

    def commit(self):
        with self._lock:
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


class RequestStore(StoreSchema):
    def __init__(self, db_path: str | Path):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Reentrant: the many `with self._lock:` blocks below wrap calls
        # that now take the same lock again inside the connection.
        self._lock = RLock()
        connection = sqlite3.connect(str(db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        self._conn = _SerializedConnection(connection, self._lock)
        self._init_schema()
        self._backfill_library_items()

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
    ) -> RequestRow:
        now = _now()
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
            self._conn.commit()
            row_id = cur.lastrowid
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
    ) -> RequestRow:
        """The Stage 12 equivalent of `create_request` for one episode of a
        subscribed show — same table, same statuses, same watcher, per the
        plan's "reuse, don't duplicate" call. `query` is always None: an
        episode request is never a free-text search, it's already fully
        identified by `show_id`/`season_number`/`episode_number`."""
        poster_path = poster_path or self._show_poster(show_id)
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (query, tmdb_id, title, release_year, status, media_type, "
                "show_id, season_number, episode_number, poster_path, min_resolution, created_at, updated_at) "
                "VALUES (NULL, ?, ?, NULL, 'queued', 'episode', ?, ?, ?, ?, ?, ?, ?)",
                (tmdb_id, title, show_id, season_number, episode_number, poster_path, min_resolution, now, now),
            )
            self._conn.commit()
            row_id = cur.lastrowid
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
        episode row (see worker.py's `_organize_and_complete_pack`)."""
        poster_path = poster_path or self._show_poster(show_id)
        now = _now()
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
            self._conn.commit()
            row_id = cur.lastrowid
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
            "SELECT * FROM requests WHERE requested_by_plex_id = ? ORDER BY id DESC LIMIT ?",
            (plex_user_id, limit),
        ).fetchall()
        return [RequestRow._from_row(r) for r in rows]

    def record_title_view(self, plex_user_id: str, media_type: str, tmdb_id: int, title: str) -> None:
        """Someone opened a title's page — a taste signal (taste.py), kept
        as one row per person per title with its latest visit."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO title_views (plex_user_id, media_type, tmdb_id, title, views, viewed_at) VALUES (?, ?, ?, ?, 1, ?) "
                "ON CONFLICT (plex_user_id, media_type, tmdb_id) DO UPDATE SET views = views + 1, viewed_at = excluded.viewed_at, title = excluded.title",
                (plex_user_id, media_type, int(tmdb_id), title, _now()),
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
                (status, error_message, json.dumps(result) if result is not None else None, _now(), request_id),
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
                (json.dumps(merged), next_attempt_at, _now(), request_id),
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
            (_now(),),
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
        now = _now()
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
            self._conn.commit()
            return cur.rowcount

    def cancel_queued_requests_for_show(self, show_id: int, season_number: int | None) -> int:
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
        business silently cancelling."""
        now = _now()
        with self._lock:
            if season_number is None:
                cur = self._conn.execute(
                    "UPDATE requests SET status = 'cancelled', updated_at = ? "
                    "WHERE show_id = ? AND status = 'queued'",
                    (now, show_id),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE requests SET status = 'cancelled', updated_at = ? "
                    "WHERE show_id = ? AND status = 'queued' AND season_number = ?",
                    (now, show_id, season_number),
                )
            self._conn.commit()
            return cur.rowcount

    # -- rejected_torrents (Stage 15) --

    def add_rejected_torrent(self, tmdb_id: int, torrent_hash: str) -> None:
        """Blacklists `torrent_hash` against `tmdb_id` — a fresh search for
        the same movie/show excludes it going forward, same as an
        already-in-qBittorrent hash (see pipeline.py's `excluded_hashes`
        parameter). `INSERT OR IGNORE`: re-rejecting the same hash (e.g. a
        duplicate API call) is a harmless no-op, not an error."""
        now = _now()
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
        now = _now()
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
                (tmdb_id, name, size_bytes, _now()),
            )
            self._conn.commit()

    def get_rejected_releases(self, tmdb_id: int) -> list[dict]:
        rows = self._conn.execute("SELECT name, size_bytes FROM rejected_releases WHERE tmdb_id = ?", (tmdb_id,)).fetchall()
        return [{"name": row["name"], "size_bytes": row["size_bytes"]} for row in rows]

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
        now = _now()
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
            self._conn.execute("UPDATE shows SET last_checked_at = ? WHERE id = ?", (_now(), show_id))
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
                (show_id, season_number, episode_number, request_id, _now(), 1 if recheck_opted_in else 0),
            )
            self._conn.commit()

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
        now = _now()
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

    # -- settings --

    def get_settings(self) -> dict:
        row = self._conn.execute("SELECT data_json FROM settings WHERE id = 1").fetchone()
        return json.loads(row["data_json"]) if row else {}

    def update_settings(self, patch: dict) -> dict:
        """Shallow-merges `patch` into the single settings row. A key set
        to `None` (e.g. unlinking Plex) is stored as null, not removed —
        callers read it back with the same `.get(...)` either way."""
        with self._lock:
            merged = {**self.get_settings(), **patch}
            self._conn.execute("UPDATE settings SET data_json = ? WHERE id = 1", (json.dumps(merged),))
            self._conn.commit()
            return merged

    # -- users / sessions (frontend migration Part C2) --

    def upsert_user(
        self,
        plex_user_id: str,
        username: str | None,
        is_admin: bool,
        avatar_url: str | None = None,
        server_token: str | None = None,
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
        "who do I go and ask about this", which is a question about now."""
        now = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT username FROM users WHERE plex_user_id = ?", (plex_user_id,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE users SET username = ?, is_admin = ?, last_login_at = ?, "
                    "avatar_url = COALESCE(?, avatar_url), "
                    "plex_server_token = COALESCE(?, plex_server_token) WHERE plex_user_id = ?",
                    (username, int(is_admin), now, avatar_url, server_token, plex_user_id),
                )
                # Only on an actual change, and never blanking a stored
                # name with a NULL Plex didn't answer with.
                if username and username != existing["username"]:
                    self._conn.execute(
                        "UPDATE requests SET requested_by_username = ? WHERE requested_by_plex_id = ?",
                        (username, plex_user_id),
                    )
            else:
                self._conn.execute(
                    "INSERT INTO users (plex_user_id, username, is_admin, has_seen_tutorial, "
                    "first_seen_at, last_login_at, avatar_url, plex_server_token) "
                    "VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
                    (plex_user_id, username, int(is_admin), now, now, avatar_url, server_token),
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
        self, session_id: str, plex_user_id: str, username: str | None, is_admin: bool, expires_at: str
    ) -> SessionRow:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, plex_user_id, username, is_admin, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, plex_user_id, username, int(is_admin), now, expires_at),
            )
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
        if session.expires_at < _now():
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

    def get_user_server_token(self, plex_user_id: str) -> str | None:
        """That user's own access token for the linked server.

        Its own method rather than a field on UserRow so it cannot be
        serialised by accident: UserRow is what /api/admin/users returns,
        and anything on it is one `**row.__dict__` away from the wire.
        Callers here want it for exactly one thing — asking Plex a question
        *as that person* — and the answer, not the token, is what travels."""
        row = self._conn.execute(
            "SELECT plex_server_token FROM users WHERE plex_user_id = ?", (plex_user_id,)
        ).fetchone()
        return row["plex_server_token"] if row else None

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
                (event_type, plex_user_id, username, ip_address, detail, _now()),
            )
            self._conn.commit()

    def list_auth_events(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM auth_events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_auth_events(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM auth_events").fetchone()[0]

    # -- setup bootstrap (frontend migration Part G1) --

    def get_or_create_setup_token(self) -> str:
        """One-time bootstrap secret required by /api/setup/* (and the
        first-run POST /api/plex/link) until initial setup completes —
        closes the race where, on an internet-reachable instance, a
        stranger who reaches an unset-up install first could claim the
        admin slot before the real admin does. Persisted in the settings
        table rather than regenerated per-process, so it survives a
        `--reload` restart during development and stays valid for as long
        as setup remains incomplete, however long that takes."""
        settings = self.get_settings()
        token = settings.get("setup_token")
        if not token:
            token = secrets.token_urlsafe(32)
            self.update_settings({"setup_token": token})
        return token

    def close(self) -> None:
        self._conn.close()
