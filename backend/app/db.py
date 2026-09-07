"""SQLite job store — request state survives backend restarts. Plain
sqlite3, no ORM (household scale, per the plan's confirmed architecture).
A single connection with `check_same_thread=False` guarded by a
`threading.Lock`; callers on the async side wrap calls in
`asyncio.to_thread` so a query never blocks the event loop."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

# Rows in these statuses are still live — an active job, or a torrent the
# download watcher is still tracking. Retention purges (automatic or the
# "Clear My Requests" button) never touch them, only settled history.
NON_TERMINAL_STATUSES = {"queued", "searching", "downloading"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RequestRow:
    id: int
    query: str | None
    tmdb_id: int
    title: str
    release_year: int | None
    status: str
    error_message: str | None
    result: dict | None
    created_at: str
    updated_at: str
    # Stage 12: an episode request reuses this same table/statuses/watcher
    # rather than a parallel one — `media_type` distinguishes the two,
    # `show_id`/`season_number`/`episode_number` are only ever set together,
    # only for `media_type == 'episode'`. `tmdb_id` for an episode row is
    # the *show's* tmdb id, same as a movie row's is the movie's.
    media_type: str = "movie"
    show_id: int | None = None
    season_number: int | None = None
    episode_number: int | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "RequestRow":
        return cls(
            id=row["id"],
            query=row["query"],
            tmdb_id=row["tmdb_id"],
            title=row["title"],
            release_year=row["release_year"],
            status=row["status"],
            error_message=row["error_message"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            media_type=row["media_type"],
            show_id=row["show_id"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
        )


@dataclass
class ShowRow:
    """A standing subscription (Stage 12) — distinct from the per-episode
    audit trail, which lives in `requests` like any other request."""

    id: int
    tmdb_id: int
    title: str
    status: str  # "watching" | "paused"
    created_at: str
    last_checked_at: str | None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "ShowRow":
        return cls(
            id=row["id"],
            tmdb_id=row["tmdb_id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            last_checked_at=row["last_checked_at"],
        )


@dataclass
class ShowEpisodeRow:
    """One entry in the per-episode dedup ledger (Stage 12) — `request_id`
    points at whichever `requests` row is the current audit trail for this
    episode (the original attempt, or the latest retry/upgrade if it's been
    rechecked). `recheck_count`/`last_rechecked_at` back worker.py's
    auto-recheck loop (Stage 12.x)."""

    id: int
    show_id: int
    season_number: int
    episode_number: int
    request_id: int
    created_at: str
    recheck_count: int
    last_rechecked_at: str | None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "ShowEpisodeRow":
        return cls(
            id=row["id"],
            show_id=row["show_id"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            request_id=row["request_id"],
            created_at=row["created_at"],
            recheck_count=row["recheck_count"],
            last_rechecked_at=row["last_rechecked_at"],
        )


class RequestStore:
    def __init__(self, db_path: str | Path):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT,
                    tmdb_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    release_year INTEGER,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Stage 12: an already-existing NAS-deployed database needs
            # these added on top of the table above, not just at CREATE
            # time — PRAGMA-checked rather than a blind ALTER, since
            # ALTER TABLE ... ADD COLUMN has no IF NOT EXISTS in the
            # sqlite3 versions this project targets.
            self._ensure_column("requests", "media_type", "media_type TEXT NOT NULL DEFAULT 'movie'")
            self._ensure_column("requests", "show_id", "show_id INTEGER")
            self._ensure_column("requests", "season_number", "season_number INTEGER")
            self._ensure_column("requests", "episode_number", "episode_number INTEGER")

            # Stage 12: the standing subscription. One row per subscribed
            # show — a UNIQUE tmdb_id stops two subscriptions to the same
            # show from ever both driving the scheduler.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tmdb_id INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_checked_at TEXT
                )
                """
            )
            # Stage 12: the per-episode dedup ledger — distinct from the
            # `requests` audit trail. UNIQUE(show_id, season_number,
            # episode_number) is what makes "already handled" a single
            # indexed lookup, and what an `INSERT OR IGNORE` relies on to
            # stay race-safe.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS show_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    show_id INTEGER NOT NULL,
                    season_number INTEGER NOT NULL,
                    episode_number INTEGER NOT NULL,
                    request_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(show_id, season_number, episode_number)
                )
                """
            )
            # Auto-recheck (retry a stuck episode, or look for a better
            # release once one's already downloaded): `recheck_count` gates
            # against a configured max-attempts, `last_rechecked_at` (falls
            # back to `created_at` when null, i.e. never rechecked) gates
            # against a configured interval — see worker.py's
            # `_watch_episode_rechecks`.
            self._ensure_column("show_episodes", "recheck_count", "recheck_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("show_episodes", "last_rechecked_at", "last_rechecked_at TEXT")
            # Stage 7's settings panel reads/writes this; Stage 3 only owns
            # the schema — a single row, not per-profile (no family
            # profiles, per the confirmed architecture).
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    data_json TEXT NOT NULL
                )
                """
            )
            self._conn.execute("INSERT OR IGNORE INTO settings (id, data_json) VALUES (1, '{}')")
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    # -- requests --

    def create_request(self, tmdb_id: int, title: str, release_year: int | None, query: str | None) -> RequestRow:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (query, tmdb_id, title, release_year, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (query, tmdb_id, title, release_year, now, now),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        return self.get_request(row_id)

    def create_episode_request(
        self, tmdb_id: int, show_id: int, title: str, season_number: int, episode_number: int
    ) -> RequestRow:
        """The Stage 12 equivalent of `create_request` for one episode of a
        subscribed show — same table, same statuses, same watcher, per the
        plan's "reuse, don't duplicate" call. `query` is always None: an
        episode request is never a free-text search, it's already fully
        identified by `show_id`/`season_number`/`episode_number`."""
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (query, tmdb_id, title, release_year, status, media_type, "
                "show_id, season_number, episode_number, created_at, updated_at) "
                "VALUES (NULL, ?, ?, NULL, 'queued', 'episode', ?, ?, ?, ?, ?)",
                (tmdb_id, title, show_id, season_number, episode_number, now, now),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        return self.get_request(row_id)

    def create_pack_request(
        self, tmdb_id: int, show_id: int, title: str, season_number: int | None
    ) -> RequestRow:
        """Stage 13: the tracking row for one bulk season/complete-series
        pack search+add attempt — reuses the `requests` table/statuses/
        watcher a third way (`media_type='pack'`), same "reuse, don't
        duplicate" call `create_episode_request` already made for Stage 12.
        `season_number` set means "season N"; left NULL means "complete
        series" — no separate `scope` column, since the two are always
        distinguishable this way. `episode_number` is always NULL: a pack
        row is never about one specific episode, only once it's organized
        does each actual episode found inside it get its own normal
        episode row (see worker.py's `_organize_and_complete_pack`)."""
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (query, tmdb_id, title, release_year, status, media_type, "
                "show_id, season_number, episode_number, created_at, updated_at) "
                "VALUES (NULL, ?, ?, NULL, 'queued', 'pack', ?, ?, NULL, ?, ?)",
                (tmdb_id, title, show_id, season_number, now, now),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        return self.get_request(row_id)

    def get_request(self, request_id: int) -> RequestRow | None:
        row = self._conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return RequestRow._from_row(row) if row else None

    def get_latest_request_for_show(self, show_id: int) -> RequestRow | None:
        """Most recent episode/pack request row for a subscribed show —
        backs the Watching list's "latest episode status" (Stage 14)."""
        row = self._conn.execute(
            "SELECT * FROM requests WHERE show_id = ? ORDER BY id DESC LIMIT 1", (show_id,)
        ).fetchone()
        return RequestRow._from_row(row) if row else None

    def list_requests(self, status: str | None = None) -> list[RequestRow]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM requests WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
        return [RequestRow._from_row(r) for r in rows]

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
        safety-filtered query rather than a separate unrestricted DELETE."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM requests WHERE created_at < ? AND status NOT IN ({placeholders})",
                (cutoff, *NON_TERMINAL_STATUSES),
            )
            self._conn.commit()
            return cur.rowcount

    # -- shows (Stage 12 standing subscriptions) --

    def create_show(self, tmdb_id: int, title: str) -> ShowRow:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO shows (tmdb_id, title, status, created_at, last_checked_at) "
                "VALUES (?, ?, 'watching', ?, NULL)",
                (tmdb_id, title, now),
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
        row = self._conn.execute(
            "SELECT 1 FROM show_episodes WHERE show_id = ? AND season_number = ? AND episode_number = ?",
            (show_id, season_number, episode_number),
        ).fetchone()
        return row is not None

    def add_show_episode(self, show_id: int, season_number: int, episode_number: int, request_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO show_episodes "
                "(show_id, season_number, episode_number, request_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (show_id, season_number, episode_number, request_id, _now()),
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

    def close(self) -> None:
        self._conn.close()
