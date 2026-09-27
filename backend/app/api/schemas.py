"""Response models shared across route areas. A model only one area uses
lives with that area instead."""

from pydantic import BaseModel

from app.db import RequestRow


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RequestOut(BaseModel):
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
    media_type: str
    show_id: int | None
    season_number: int | None
    episode_number: int | None
    season_range_end: int | None
    # Frontend migration Part C2 — who asked for this (None for
    # worker-created rows, e.g. a subscribed show's automatic catch-up).
    requested_by_plex_id: str | None = None
    requested_by_username: str | None = None
    # Frontend migration Part K1/K3 — "upgrade"/"overwrite"/None, drives
    # the queue's "Redownload" tag.
    redownload_mode: str | None = None
    # Frontend migration Part J1/J2 — poster art and live download
    # progress for the Requests queue, both denormalized/refreshed the
    # same way redownload_mode's neighbors above already are.
    poster_path: str | None = None
    download_progress: float | None = None

    @classmethod
    def from_row(cls, row: RequestRow) -> "RequestOut":
        return cls(**row.__dict__)
