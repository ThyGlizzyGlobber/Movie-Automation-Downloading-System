"""RequestStore's schema: the tables it creates, and the in-place
migrations that bring a database file written by an older version up to
date. Runs once, from RequestStore's constructor."""

import json

from app import config


class StoreSchema:
    """Mixed into RequestStore rather than being an object of its own, so
    RequestStore stays the one class callers see. Relies on its `_conn`
    and `_lock`."""

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
            self._ensure_column("requests", "season_range_end", "season_range_end INTEGER")
            self._ensure_column("requests", "source_cleanup_status", "source_cleanup_status TEXT")
            self._ensure_column(
                "requests", "source_cleanup_next_attempt_at", "source_cleanup_next_attempt_at TEXT"
            )
            self._ensure_column("requests", "min_resolution", "min_resolution TEXT")
            # Frontend migration Part C2: who asked for this — see
            # RequestRow's own field comments.
            self._ensure_column("requests", "requested_by_plex_id", "requested_by_plex_id TEXT")
            self._ensure_column("requests", "requested_by_username", "requested_by_username TEXT")
            # Frontend migration Part K2 — see RequestRow's own comment.
            self._ensure_column("requests", "redownload_mode", "redownload_mode TEXT")
            # Frontend migration Part J1/J2 — see RequestRow's own comments.
            self._ensure_column("requests", "poster_path", "poster_path TEXT")
            self._ensure_column("requests", "download_progress", "download_progress REAL")

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
            # Frontend migration Part J1 — see ShowRow's own comment.
            self._ensure_column("shows", "poster_path", "poster_path TEXT")
            self._ensure_column("shows", "tmdb_status", "tmdb_status TEXT")
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
            # Whether this episode was claimed while "keep looking for
            # missing or better copies" was on. Recorded per episode
            # rather than read live, because the setting answers a
            # question about the future and not the past: turning it on
            # used to make every episode ever downloaded immediately due
            # — `_recheck_is_due` falls back to `created_at` when nothing
            # has been rechecked, and while the setting is off nothing
            # ever is — so a household that enabled it came back to find
            # its whole library being re-searched for upgrades it never
            # asked for.
            #
            # Existing rows are backfilled from the setting as it stands
            # right now, which is the only evidence available about how
            # they were created: a household with it on keeps the
            # behaviour it has today, and one with it off is left alone,
            # which is the point.
            if self._ensure_column("show_episodes", "recheck_opted_in", "recheck_opted_in INTEGER NOT NULL DEFAULT 0"):
                if self._recheck_enabled_now():
                    self._conn.execute("UPDATE show_episodes SET recheck_opted_in = 1")
            # Stage 15: torrents explicitly rejected as genuinely defective
            # (bad encode, audio sync drift, wrong cut — anything the
            # search/scoring pipeline's filename-based signals could never
            # have caught up front, confirmed live via a well-seeded,
            # top-scored "Mutiny" 2160p release with progressive audio
            # sync drift, 2026-09-14). Keyed by tmdb_id, not request_id —
            # a rejection needs to keep excluding this exact torrent from
            # every *future* request for the same movie/show, not just the
            # one that first found it. UNIQUE(tmdb_id, torrent_hash) makes
            # re-rejecting the same hash a harmless no-op.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rejected_torrents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tmdb_id INTEGER NOT NULL,
                    torrent_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(tmdb_id, torrent_hash)
                )
                """
            )
            # The library ledger: every file this app filed, kept for as long
            # as the file is — clearing request history never touches it.
            # This, not the request row, is how the app knows a title on
            # Plex is one it added, which release it was, and where it is.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS library_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tmdb_id INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    season_number INTEGER,
                    episode_number INTEGER,
                    path TEXT NOT NULL UNIQUE,
                    torrent_hash TEXT,
                    release_name TEXT,
                    size_bytes INTEGER,
                    request_id INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rejected_releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tmdb_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    size_bytes INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(tmdb_id, name)
                )
                """
            )
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

            # Frontend migration Part C2 — Plex-authenticated users and
            # their signed-in sessions. See UserRow/SessionRow in db_rows.py.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    plex_user_id TEXT PRIMARY KEY,
                    username TEXT,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    has_seen_tutorial INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    plex_user_id TEXT NOT NULL,
                    username TEXT,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            # Household controls per user.
            self._ensure_column("users", "can_request", "can_request INTEGER NOT NULL DEFAULT 1")
            self._ensure_column("users", "avatar_url", "avatar_url TEXT")
            # That user's *own* access token for the linked server, kept so
            # per-user Plex reads (Continue Watching) can be made as them
            # rather than as the admin. Deliberately not a field on UserRow:
            # that dataclass is what /api/admin/users serialises, and a token
            # that never enters the object can't leave in a response. Read it
            # through get_user_server_token() instead, which exists to be the
            # one path.
            self._ensure_column("users", "plex_server_token", "plex_server_token TEXT")
            # The notification system was removed: drop its tables from
            # databases created before that.
            self._conn.execute("DROP TABLE IF EXISTS notifications")
            self._conn.execute("DROP TABLE IF EXISTS push_subscriptions")
            # TV rows created without a poster (episode rechecks did) take
            # the show's, or any other row's for the same title.
            self._conn.execute(
                "UPDATE requests SET poster_path = COALESCE("
                "(SELECT s.poster_path FROM shows s WHERE s.id = requests.show_id), "
                "(SELECT r.poster_path FROM requests r WHERE r.tmdb_id = requests.tmdb_id "
                "AND r.media_type != 'movie' AND r.poster_path IS NOT NULL LIMIT 1)) "
                "WHERE poster_path IS NULL AND media_type != 'movie'"
            )
            # Frontend migration Part G4 — a plain append-only log an admin
            # can actually check once this instance is internet-facing.
            # Scoped to genuinely security-relevant events, not every
            # settings change (Pipeline/TV-schedule/Retention tuning isn't
            # a security event) — login success/failure, Plex server
            # link/switch/unlink, Connections (TMDB/qBittorrent
            # credential) changes, Remote Access toggles, and deploy
            # triggers. `detail` is a short human-readable string, not
            # structured JSON — this is read by a person, not parsed back.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    plex_user_id TEXT,
                    username TEXT,
                    ip_address TEXT,
                    detail TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Which titles each person has opened in the app — the third
            # taste signal beside Plex watch history and requests (see
            # taste.py). One row per person per title: what matters is that
            # and when they last looked, not every visit, and a title opened
            # fifty times is one interest rather than fifty.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS title_views (
                    plex_user_id TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    tmdb_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    views INTEGER NOT NULL DEFAULT 1,
                    viewed_at TEXT NOT NULL,
                    PRIMARY KEY (plex_user_id, media_type, tmdb_id)
                )
                """
            )
            self._conn.commit()

    def _recheck_enabled_now(self) -> bool:
        """The saved value of `episode_recheck_enabled`, falling back to
        config's default when it has never been set — the same precedence
        tv_settings.settings_from_raw uses. Read straight off the table
        rather than through get_settings(), because this runs inside
        schema setup with the lock already held."""
        # Schema setup runs this before the settings table itself is
        # created on a brand-new database. There is nothing saved to read
        # in that case, and nothing to back-fill either — a fresh file has
        # no episodes.
        table = self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'settings'").fetchone()
        row = self._conn.execute("SELECT data_json FROM settings WHERE id = 1").fetchone() if table else None
        saved = json.loads(row["data_json"]) if row else {}
        value = saved.get("episode_recheck_enabled")
        return bool(config.EPISODE_RECHECK_ENABLED if value is None else value)

    def _ensure_column(self, table: str, column: str, ddl: str) -> bool:
        existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            return True
        return False
