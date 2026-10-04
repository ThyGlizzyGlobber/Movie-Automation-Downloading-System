"""TV: standing show subscriptions, whole-season/series bulk downloads,
a season's episode list and one-episode requests."""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel, field_validator, model_validator

from app.db import FAILED_OR_CANCELLED_STATUSES, NON_TERMINAL_STATUSES, RequestRow, RequestStore, SessionRow, ShowRow
from app.media_organizer import find_existing_episode_files
from app.tmdb import TMDBClient, TMDBError
from app.tv_resolve import ShowIdentity, episode_is_released, resolve_show
from app.tv_settings import resolve_tv_settings
from app.tvmaze import TVMazeClient, season_airstamps
from app.worker import Worker
from app.api.deps import get_store, get_tmdb, get_worker, logger, require_can_request, router
from app.api.schemas import RequestOut


class SubscribeShowRequest(BaseModel):
    tmdb_id: int


class BulkDownloadRequest(BaseModel):
    """Stage 13: `{"scope": "series"}` or `{"scope": "season", "season_number": N}`.
    Deliberately its own request, distinct from `SubscribeShowRequest` — a
    bulk download is a one-shot user action, independent of (not a
    replacement for) the standing per-episode subscription."""

    scope: str
    season_number: int | None = None
    # Frontend migration Part K3 — TV gets the same redownload treatment
    # as movies. Accepted and stored for a pack request, but the actual
    # overwrite-deletion mechanism (Part K2) is movie-only for now; an
    # "overwrite" pack request behaves like "upgrade" until that's built
    # out for packs too — a named, not-yet-solved gap in the same style
    # as this project's others, not silently pretended to be complete.
    redownload_mode: str | None = None

    @field_validator("scope")
    @classmethod
    def _known_scope(cls, v: str) -> str:
        if v not in ("season", "series"):
            raise ValueError("scope must be 'season' or 'series'")
        return v

    @field_validator("redownload_mode")
    @classmethod
    def _known_redownload_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("upgrade", "overwrite"):
            raise ValueError("redownload_mode must be 'upgrade' or 'overwrite'")
        return v

    @model_validator(mode="after")
    def _season_number_matches_scope(self) -> "BulkDownloadRequest":
        if self.scope == "season" and self.season_number is None:
            raise ValueError("season_number is required when scope is 'season'")
        if self.scope == "series" and self.season_number is not None:
            raise ValueError("season_number must not be set when scope is 'series'")
        return self


class ShowOut(BaseModel):
    id: int
    tmdb_id: int
    title: str
    status: str
    created_at: str
    last_checked_at: str | None
    # Frontend migration Part J1 — see RequestRow's own comment.
    poster_path: str | None = None
    # TMDB's status at the last follow check (see db.ShowRow.tmdb_status).
    tmdb_status: str | None = None
    # Stage 14: the Watching list's "latest episode status" — the most
    # recent episode/pack request this show has produced, or None if it
    # hasn't been checked yet (e.g. just subscribed, catch-up still queued).
    latest_request: RequestOut | None = None

    @classmethod
    def from_row(cls, row: ShowRow, latest_request: RequestOut | None = None) -> "ShowOut":
        return cls(**row.__dict__, latest_request=latest_request)


def _show_out(store: RequestStore, row: ShowRow) -> ShowOut:
    latest = store.get_latest_request_for_show(row.id)
    return ShowOut.from_row(row, latest_request=RequestOut.from_row(latest) if latest else None)


_tvmaze = TVMazeClient()


def _season_airstamps_for(identity: ShowIdentity | None, season_number: int) -> dict[int, datetime]:
    """`{episode_number: released_at_utc}` from TVmaze for one season, so
    the show page and the per-episode request route hold an episode for
    the same window the worker does — measured from its real release
    rather than midnight UTC on TMDB's date. `{}` on any failure (the
    caller's own `resolve_show` included, passed as `None`), which falls
    back to the date rule (tv_resolve.episode_is_released)."""
    if identity is None:
        return {}
    return season_airstamps(_tvmaze.airstamps_for_show(identity.tvdb_id, identity.imdb_id), season_number)


# -- Stage 12: standing show subscriptions. POST creates the subscription
#    and immediately runs a *full* backfill catch-up (Stage 14.x — every
#    season from 1 through the current latest, not just the latest), so
#    subscribing to a show with nothing downloaded at all grabs everything
#    already aired, not only its newest season. Every subsequent scheduled
#    recheck (worker.py's `_check_all_watching_shows`) keeps checking only
#    the latest season, same as always — this full sweep only ever runs
#    once, right here, at the moment of subscribing. --


@router.post("/api/shows", status_code=201)
def create_show(
    body: SubscribeShowRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
) -> ShowOut:
    existing = store.get_show_by_tmdb_id(body.tmdb_id)
    if existing is not None and existing.status == "watching":
        raise HTTPException(status_code=409, detail="already following this show")
    if existing is not None:
        # A paused row is the anchor a one-off season add left behind;
        # following the show now is just switching that row on.
        store.update_show_status(existing.id, "watching")
        row = store.get_show(existing.id)
    else:
        try:
            identity = resolve_show(body.tmdb_id, tmdb)
        except TMDBError as exc:
            raise HTTPException(status_code=404, detail=f"tmdb_id {body.tmdb_id} not found") from exc
        row = store.create_show(tmdb_id=body.tmdb_id, title=identity.title, poster_path=identity.poster_path)
    worker.check_show(row, full_backfill=True)
    return _show_out(store, store.get_show(row.id))


@router.get("/api/shows")
def list_shows(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[ShowOut]:
    return [_show_out(store, r) for r in store.list_shows(status=status)]


@router.get("/api/shows/{show_id}")
def get_show(show_id: int, store: RequestStore = Depends(get_store)) -> ShowOut:
    row = store.get_show(show_id)
    if row is None:
        raise HTTPException(status_code=404, detail="show not found")
    return _show_out(store, row)


@router.delete("/api/shows/{show_id}")
def unsubscribe_show(show_id: int, store: RequestStore = Depends(get_store)) -> dict:
    """Unsubscribes — stops future checks. Every `requests`/`show_episodes`
    row this show ever produced stays exactly as it was, same "hidden,
    never unrecoverable" principle as cancelling a movie request."""
    if not store.delete_show(show_id):
        raise HTTPException(status_code=404, detail="show not found")
    return {"deleted": True}


# -- Stage 13: whole-season / complete-series bulk acquisition. A plain,
#    explicit user action distinct from subscribing above —
#    usable regardless of a show's watching/paused status, and regardless
#    of whether it's still airing, per the plan's "independent, not
#    mutually exclusive" call. Goes through the same requests table/worker
#    queue as every other download (`media_type='pack'`), so it never races
#    a queued movie/episode search — see worker.py's `_run_one`.
#
#    Stage 14.x: keyed by `tmdb_id`, not a local `shows.id` — bulk
#    download is meant to work whether or not the show has ever been
#    subscribed (e.g. "just grab me the one season already out, I don't
#    want a standing subscription"). If no `shows` row exists yet, one is
#    created here with status "paused" rather than "watching" — anchoring
#    the request without opting the show into the standing per-episode
#    catch-up/recheck, which only ever looks at "watching" rows. A show
#    that's already subscribed (watching or paused) is used as-is; its
#    status is never touched by this route. --


@router.post("/api/tv/{tmdb_id}/bulk-download", status_code=201)
def bulk_download_show(
    tmdb_id: int,
    body: BulkDownloadRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
    session: SessionRow = Depends(require_can_request),
) -> RequestOut:
    try:
        identity = resolve_show(tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
    show = store.get_show_by_tmdb_id(tmdb_id)
    if show is None:
        show = store.create_show(
            tmdb_id=tmdb_id, title=identity.title, status="paused", poster_path=identity.poster_path
        )
    # "Add all to Plex" on a show that is still going also follows it, so
    # the episodes that haven't aired yet keep arriving; a one-off season
    # add, or a show that has ended, leaves following alone.
    if body.scope == "series" and not identity.ended and show.status != "watching":
        store.update_show_status(show.id, "watching")
        show = store.get_show(show.id)
        logger.info("show %d (%s): whole-series add on a returning show — now following", show.id, show.title)

    # A bulk download makes this show's own still-queued requests it
    # covers redundant — series scope covers every season, a season scope
    # only that same season — see db.py's cancel_queued_requests_for_show
    # for exactly what's touched (queued only; never a request already
    # searching/downloading/complete). Without this, subscribing to a show
    # (which immediately queues catch-up requests for its latest season)
    # and then asking for a bulk download of the same show left both
    # running at once, flooding the requests list with soon-redundant rows.
    cancelled = store.cancel_queued_requests_for_show(show.id, body.season_number)
    if cancelled:
        logger.info(
            "show %d (%s): bulk-download (%s) cancelled %d still-queued request(s) it supersedes",
            show.id, show.title, body.scope, cancelled,
        )

    row = store.create_pack_request(
        tmdb_id=show.tmdb_id,
        show_id=show.id,
        title=show.title,
        season_number=body.season_number,
        requested_by_plex_id=session.plex_user_id,
        requested_by_username=session.username,
        # No on_plex_tracked/400 gate here unlike the movie route — TV's
        # actual overwrite-deletion mechanism isn't built yet (see
        # BulkDownloadRequest's own docstring), so there's nothing yet
        # for "overwrite" to unsafely bypass; it's accepted and stored,
        # but behaves like "upgrade" until that's built out.
        redownload_mode=body.redownload_mode,
        poster_path=show.poster_path,
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


@router.get("/api/tv/{tmdb_id}/season/{season_number}/episodes")
def get_season_episodes(
    tmdb_id: int,
    season_number: int,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    """One season's episodes with what this household has of each —
    `in_plex` (a request this app filed, or a matching file already under
    the TV library), `requested` (a live per-episode or covering pack
    request, with its status/progress), `failed` (the last attempt ended
    badly), `unaired`, or `missing`. Backs the show page's episode list."""
    try:
        episodes = tmdb.get_tv_season(tmdb_id, season_number)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"season {season_number} of tmdb_id {tmdb_id} not found") from exc
    show = store.get_show_by_tmdb_id(tmdb_id)
    per_episode: dict[int, RequestRow] = {}
    covering_packs: list[RequestRow] = []
    if show is not None:
        ledgers = [ledger for ledger in store.list_show_episodes(show.id) if ledger.season_number == season_number]
        rows_by_id = store.get_requests(ledger.request_id for ledger in ledgers)
        for ledger in ledgers:
            row = rows_by_id.get(ledger.request_id)
            if row is not None:
                per_episode[ledger.episode_number] = row
        for row in store.list_live_packs_for_show(show.id):
            start = row.season_number
            end = row.season_range_end if row.season_range_end is not None else start
            if start is None or (start <= season_number <= end):
                covering_packs.append(row)
    numbers = [e.get("episode_number") for e in episodes if e.get("episode_number") is not None]
    on_disk: dict[int, object] = {}
    identity = None
    try:
        identity = resolve_show(tmdb_id, tmdb)
        on_disk = find_existing_episode_files(identity, season_number, numbers)
    except (TMDBError, OSError):
        on_disk = {}
    # The air buffer the subscription scheduler honours, applied here too.
    # Without it this list called an episode requestable from UTC midnight
    # on its air_date while the scheduler wouldn't touch it for hours yet,
    # so the show page offered an "Add to Plex" button straight into the
    # exact window the setting exists to sit out.
    today = datetime.now(timezone.utc).date().isoformat()
    buffer_hours = resolve_tv_settings(store).episode_air_buffer_hours
    stamps = _season_airstamps_for(identity, season_number) if buffer_hours else {}
    out = []
    for ep in episodes:
        number = ep.get("episode_number")
        row = per_episode.get(number)
        air_date = ep.get("air_date")
        state, status, progress, request_id = "missing", None, None, None
        if row is not None and row.status == "complete":
            state, status, request_id = "in_plex", row.status, row.id
        elif number in on_disk:
            state = "in_plex"
        elif row is not None and row.status in NON_TERMINAL_STATUSES:
            state, status, progress, request_id = "requested", row.status, row.download_progress, row.id
        elif covering_packs:
            pack = covering_packs[0]
            state, status, progress, request_id = "requested", pack.status, pack.download_progress, pack.id
        elif row is not None and row.status in FAILED_OR_CANCELLED_STATUSES:
            state, status, request_id = "failed", row.status, row.id
        elif not air_date or air_date > today:
            state = "unaired"
        elif not episode_is_released(air_date, stamps.get(number), buffer_hours=buffer_hours):
            # Out, but inside the air buffer — the scheduler is
            # deliberately waiting, so the page says so rather than
            # offering a button that would jump the queue.
            state = "holding"
        out.append(
            {
                "episode_number": number,
                "name": ep.get("name"),
                "overview": ep.get("overview"),
                "air_date": air_date,
                # TVmaze's exact release moment, when it has one. The date
                # alone can't tell the page whether a held episode has
                # come out yet or is still due later the same day.
                "airs_at": stamps[number].isoformat() if number in stamps else None,
                "runtime": ep.get("runtime"),
                "still_path": ep.get("still_path"),
                "state": state,
                "status": status,
                "download_progress": progress,
                "request_id": request_id,
            }
        )
    in_plex = sum(1 for e in out if e["state"] == "in_plex")
    aired = sum(1 for e in out if e["state"] != "unaired")
    return {"season_number": season_number, "episodes": out, "in_plex": in_plex, "aired": aired}


@router.post("/api/tv/{tmdb_id}/episodes/{season_number}/{episode_number}", status_code=201)
def request_episode(
    tmdb_id: int,
    season_number: int,
    episode_number: int,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
    session: SessionRow = Depends(require_can_request),
) -> RequestOut:
    """Ask for one specific episode (the show page's per-row Request
    button). Same ledger the subscription scheduler uses, so the two never
    double-request the same episode; 409 if it is already tracked."""
    show = store.get_show_by_tmdb_id(tmdb_id)
    identity = None
    if show is None:
        try:
            identity = resolve_show(tmdb_id, tmdb)
        except TMDBError as exc:
            raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc
        show = store.create_show(tmdb_id=tmdb_id, title=identity.title, status="paused", poster_path=identity.poster_path)
    if store.has_show_episode(show.id, season_number, episode_number):
        raise HTTPException(status_code=409, detail="this episode is already tracked")

    # The air buffer is a safety setting, not a scheduling preference: it
    # exists because the hours right after an air_date rolls over are when
    # fake and empty releases get uploaded to catch automated tools
    # searching too early. Enforcing it only in the worker left this route
    # as a way around it — and the show page was offering the button.
    buffer_hours = resolve_tv_settings(store).episode_air_buffer_hours
    if buffer_hours:
        try:
            episodes = tmdb.get_tv_season(tmdb_id, season_number)
        except TMDBError:
            episodes = []
        air_date = next(
            (e.get("air_date") for e in episodes if e.get("episode_number") == episode_number), None
        )
        if identity is None:
            try:
                identity = resolve_show(tmdb_id, tmdb)
            except TMDBError:
                pass
        stamps = _season_airstamps_for(identity, season_number)
        if air_date and not episode_is_released(air_date, stamps.get(episode_number), buffer_hours=buffer_hours):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"this episode aired too recently — it's held for {buffer_hours:g}h after its air date "
                    "so a real release has time to appear (Settings › TV scheduling)"
                ),
            )

    row = store.create_episode_request(
        tmdb_id=show.tmdb_id,
        show_id=show.id,
        title=show.title,
        season_number=season_number,
        episode_number=episode_number,
        poster_path=show.poster_path,
    )
    store.add_show_episode(show.id, season_number, episode_number, row.id)
    logger.info(
        "episode request %d: %s S%02dE%02d by %s", row.id, show.title, season_number, episode_number, session.username
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)
