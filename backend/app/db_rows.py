"""The row types RequestStore reads back: one dataclass per table a caller
ever sees, each built from a `sqlite3.Row` by its own `_from_row`."""

import json
import sqlite3
from dataclasses import dataclass


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
    # Stage 14.x: only ever set on a 'pack' row, alongside season_number as
    # the range's start — season_number set with this left NULL still means
    # "one season" (Stage 13's original shape), unchanged; both set means
    # "seasons season_number through season_range_end inclusive".
    season_range_end: int | None = None
    # Durable, restart-safe post-organize source cleanup (replaces Stage
    # 13.x's original fire-and-forget in-memory version — see
    # worker.py's _watch_source_cleanup). NULL means "not applicable"
    # (every row created before this existed, or a row that was never
    # organized); 'pending' means a cleanup attempt is owed once
    # `source_cleanup_next_attempt_at` arrives; 'done' means it either
    # succeeded or was deliberately, permanently skipped (see
    # worker.py's `_attempt_source_cleanup`) — either way, never retried
    # again. `result["organized_paths"]`/`result["pending_cleanup_hashes"]`
    # (plain JSON inside the existing result blob, not new columns) carry
    # what actually needs cleaning up.
    source_cleanup_status: str | None = None
    source_cleanup_next_attempt_at: str | None = None
    # Per-request floor override (movie requests only, from the detail
    # page's "Download 4K"/"Download 1080p" shortcuts) — a canonical
    # min_resolution phrase (see config.RESOLUTION_TIERS) applied on top
    # of the global pipeline settings for this one request. NULL means
    # "use the global default", same as every row created before this
    # existed.
    min_resolution: str | None = None
    # Who asked for this — the authenticated Plex account at the moment
    # the request was created (frontend migration Part C2). Denormalized
    # (copied at creation time, not joined against `users`) same as
    # title/release_year above, so the Activity Dashboard shows who
    # requested something as of *then* even if that Plex account's
    # display name later changes. NULL for every row created before this
    # existed, and for any request the worker itself creates
    # automatically (e.g. a subscribed show's per-episode catch-up) —
    # there's no authenticated user behind those, only a real request
    # made through the API has one.
    requested_by_plex_id: str | None = None
    requested_by_username: str | None = None
    # frontend migration Part K2 — "upgrade" (search again, no deletion)
    # or "overwrite" (also delete the prior organized file once this
    # request's own replacement is confirmed in place). NULL for every
    # ordinary request, same as every field added before this existed.
    redownload_mode: str | None = None
    # Frontend migration Part J1 — denormalized from the identity resolved
    # at creation time (same convention as title/release_year), so the
    # Requests queue can show poster art without a per-row TMDB fetch.
    # NULL for every row created before this existed, and whenever TMDB
    # itself has no poster on file.
    poster_path: str | None = None
    # Frontend migration Part J2 — qBittorrent's live progress fraction
    # (0.0-1.0), refreshed on every download-watcher poll
    # (DOWNLOAD_POLL_INTERVAL_SECONDS) while status == "downloading".
    # NULL before the first poll, and for any row never in that status.
    download_progress: float | None = None

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
            season_range_end=row["season_range_end"],
            source_cleanup_status=row["source_cleanup_status"],
            source_cleanup_next_attempt_at=row["source_cleanup_next_attempt_at"],
            min_resolution=row["min_resolution"],
            requested_by_plex_id=row["requested_by_plex_id"],
            requested_by_username=row["requested_by_username"],
            redownload_mode=row["redownload_mode"],
            poster_path=row["poster_path"],
            download_progress=row["download_progress"],
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
    # Frontend migration Part J1 — see RequestRow's own comment; populated
    # once at subscribe time, read back (not re-fetched) by every episode/
    # pack request this show later produces and by bulk-download for an
    # already-subscribed show.
    poster_path: str | None = None
    # TMDB's own status as of the last follow check ("Returning Series",
    # "Ended", "Canceled"…), so the app can tell a finished show from one
    # still going without asking TMDB per row.
    tmdb_status: str | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "ShowRow":
        return cls(
            id=row["id"],
            tmdb_id=row["tmdb_id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            last_checked_at=row["last_checked_at"],
            poster_path=row["poster_path"],
            tmdb_status=row["tmdb_status"] if "tmdb_status" in row.keys() else None,
        )


@dataclass
class ShowEpisodeRow:
    """One entry in the per-episode dedup ledger (Stage 12) — `request_id`
    points at whichever `requests` row is the current audit trail for this
    episode (the original attempt, or the latest retry/upgrade if it's been
    rechecked). `recheck_count`/`last_rechecked_at` back worker.py's
    auto-recheck loop (Stage 12.x), and `recheck_opted_in` records
    whether that loop is entitled to touch this episode at all — see the
    column's own note in the schema."""

    id: int
    show_id: int
    season_number: int
    episode_number: int
    request_id: int
    created_at: str
    recheck_opted_in: bool
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
            recheck_opted_in=bool(row["recheck_opted_in"]),
            recheck_count=row["recheck_count"],
            last_rechecked_at=row["last_rechecked_at"],
        )


@dataclass
class UserRow:
    """One Plex account that has ever successfully signed in (frontend
    migration Part C2) — distinct from `sessions` below: a user can have
    zero, one, or several active sessions, but only one `users` row.
    `is_admin` is refreshed on every login from a fresh Plex access check
    (see api/auth.py's login flow) — never edited directly."""

    plex_user_id: str
    username: str | None
    is_admin: bool
    has_seen_tutorial: bool
    first_seen_at: str
    last_login_at: str
    # Household controls (Settings › Household).
    can_request: bool = True
    # plex.tv avatar URL (no query string), refreshed on every sign-in.
    avatar_url: str | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "UserRow":
        keys = row.keys()
        return cls(
            plex_user_id=row["plex_user_id"],
            username=row["username"],
            is_admin=bool(row["is_admin"]),
            has_seen_tutorial=bool(row["has_seen_tutorial"]),
            first_seen_at=row["first_seen_at"],
            last_login_at=row["last_login_at"],
            can_request=bool(row["can_request"]) if "can_request" in keys else True,
            avatar_url=row["avatar_url"] if "avatar_url" in keys else None,
        )


@dataclass
class SessionRow:
    """A signed-in browser session (frontend migration Part C2/C3) — `id`
    is the opaque, random token set as the session cookie's value, never
    guessable/sequential. `username`/`is_admin` are copied from `users` at
    login time (not joined on every request) so `require_session` is a
    single indexed lookup, same denormalize-for-cheap-reads convention as
    `RequestRow.title`/`release_year`."""

    id: str
    plex_user_id: str
    username: str | None
    is_admin: bool
    created_at: str
    expires_at: str

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "SessionRow":
        return cls(
            id=row["id"],
            plex_user_id=row["plex_user_id"],
            username=row["username"],
            is_admin=bool(row["is_admin"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )
