"""FastAPI wrapper around Stages 1-2's library code, plus the SQLite job
store. Same-origin only (confirmed architecture) — no CORS middleware; the
frontend (Stage 4) reaches this only via nginx's reverse proxy on the same
origin. TMDB key and qBittorrent credentials never reach the browser: every
route here is either a thin TMDB proxy or reads/writes the local job store."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app import config
from app.db import RequestRow, RequestStore
from app.plex import PlexError, PlexLinker
from app.qbt import QBTClient
from app.resolve import resolve
from app.tmdb import TMDBClient, TMDBError
from app.worker import Worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = RequestStore(config.DB_PATH)
    app.state.tmdb = TMDBClient(config.TMDB_API_KEY)
    app.state.qbt = QBTClient(config.QBIT_HOST, config.QBIT_PORT, config.QBIT_USERNAME, config.QBIT_PASSWORD)
    app.state.worker = Worker(app.state.store, app.state.tmdb, app.state.qbt)
    app.state.plex_linker = PlexLinker(app.state.store)
    await app.state.worker.start()
    try:
        yield
    finally:
        await app.state.worker.stop()
        app.state.store.close()


app = FastAPI(title="The Family Downloader", lifespan=lifespan)


def get_store(request: Request) -> RequestStore:
    return request.app.state.store


def get_tmdb(request: Request) -> TMDBClient:
    return request.app.state.tmdb


def get_worker(request: Request) -> Worker:
    return request.app.state.worker


def get_qbt(request: Request) -> QBTClient:
    return request.app.state.qbt


def get_plex_linker(request: Request) -> PlexLinker:
    return request.app.state.plex_linker


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    year: int | None = None
    provider_id: int | None = None


class CreateRequest(BaseModel):
    tmdb_id: int
    query: str | None = None


class RetentionSettings(BaseModel):
    days: int | None = None  # None/0 = keep forever


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

    @classmethod
    def from_row(cls, row: RequestRow) -> "RequestOut":
        return cls(**row.__dict__)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/api/search")
def search(body: SearchRequest, tmdb: TMDBClient = Depends(get_tmdb)) -> list[dict]:
    try:
        if body.provider_id is not None:
            data = tmdb.search_within_provider(body.query, body.provider_id)
        else:
            data = tmdb.search_movie(body.query, year=body.year)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data.get("results", [])


# -- Stage 4: the browse surface the home grid and provider rows are built
#    from. Thin pass-throughs of tmdb.py's already-TTL-cached methods —
#    same "key never reaches the browser" rule as /api/search. --


@app.get("/api/discover/popular")
def discover_popular(page: int = 1, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    # Digital-availability filtered: Coming Soon is the dedicated tab for
    # theatrical-only titles, so Discover shouldn't also surface them.
    try:
        return tmdb.get_available_popular(page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/discover/trending")
def discover_trending(time_window: str = "week", tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    try:
        return tmdb.get_available_trending(time_window=time_window)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/discover/providers")
def discover_providers(region: str = "US", tmdb: TMDBClient = Depends(get_tmdb)) -> list[dict]:
    try:
        data = tmdb.get_watch_providers(region=region)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data.get("results", [])


@app.get("/api/discover/providers/{provider_id}")
def discover_by_provider(
    provider_id: int, region: str = "US", page: int = 1, tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        return tmdb.discover_by_provider(provider_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/discover/coming-soon")
def discover_coming_soon(region: str = "US", page: int = 1, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    try:
        return tmdb.get_coming_soon(region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/movies/{tmdb_id}")
def get_movie_detail(tmdb_id: int, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """Full TMDB detail for the detail view — overview, runtime, genres,
    poster/backdrop paths. The frontend hotlinks poster/backdrop images
    straight from TMDB's CDN using the paths returned here."""
    try:
        return tmdb.get_movie(tmdb_id)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc


@app.post("/api/requests", status_code=201)
def create_request(
    body: CreateRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
) -> RequestOut:
    try:
        identity = resolve(body.tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {body.tmdb_id} not found") from exc

    row = store.create_request(
        tmdb_id=body.tmdb_id,
        title=identity.title,
        release_year=identity.release_year,
        query=body.query,
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


@app.get("/api/requests")
def list_requests(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[RequestOut]:
    return [RequestOut.from_row(r) for r in store.list_requests(status=status)]


@app.get("/api/requests/{request_id}")
def get_request(request_id: int, store: RequestStore = Depends(get_store)) -> RequestOut:
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    return RequestOut.from_row(row)


@app.post("/api/requests/clear")
def clear_requests(store: RequestStore = Depends(get_store)) -> dict:
    """"Clear My Requests": wipes settled history (reuses the same
    active-job-safe query the automatic retention cleanup runs, with
    days=0 so age never excludes anything terminal)."""
    return {"removed": store.purge_requests_older_than(days=0)}


# A request can only be cancelled once it actually has a torrent in
# qBittorrent to delete — "queued"/"searching" haven't added anything yet,
# and every other status is already terminal.
_CANCELLABLE_STATUSES = {"downloading", "complete"}


@app.post("/api/requests/{request_id}/cancel")
def cancel_request(
    request_id: int, store: RequestStore = Depends(get_store), qbt: QBTClient = Depends(get_qbt)
) -> RequestOut:
    """Cancel from the app's own UI: deletes the torrent *and its
    downloaded files* from qBittorrent (fail safe, not best guess — never
    silently leave orphaned media on disk), then marks the request
    "cancelled". The row itself stays — this is "download history", not a
    queue, per the "hidden, never unrecoverable" principle.

    If qBittorrent no longer has the torrent at all — most commonly
    because "remove torrent after completion" already auto-removed it —
    there is nothing left to delete, and this app has no other path to the
    file (it never mounts the download folder or the Docker socket, per
    the confirmed architecture). Rather than mark the request "cancelled"
    and imply files were removed when nothing was touched, this fails
    loudly and leaves the request exactly as it was."""
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    if row.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"cannot cancel a request in status {row.status!r}")
    torrent_hash = (row.result or {}).get("torrent_hash")
    if not torrent_hash:
        raise HTTPException(status_code=409, detail="no torrent on record for this request")
    if qbt.torrent_info(torrent_hash) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "qBittorrent no longer has this torrent — it most likely finished and was "
                "auto-removed. Nothing was deleted; if the file is still on disk, it needs to "
                "be removed manually."
            ),
        )
    qbt.delete_torrent(torrent_hash, delete_files=True)
    store.update_status(request_id, "cancelled")
    return RequestOut.from_row(store.get_request(request_id))


@app.get("/api/settings/retention")
def get_retention(store: RequestStore = Depends(get_store)) -> dict:
    return {"days": store.get_settings().get("request_retention_days")}


@app.put("/api/settings/retention")
def set_retention(body: RetentionSettings, store: RequestStore = Depends(get_store)) -> dict:
    store.update_settings({"request_retention_days": body.days})
    return {"days": body.days}


# -- Plex account linking (PIN sign-in). The resulting token is stored
#    server-side only — these routes never return it. See plex.py. --


@app.post("/api/plex/link")
async def start_plex_link(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    try:
        auth_url = await linker.start()
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"auth_url": auth_url}


@app.get("/api/plex/status")
def plex_status(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    return linker.status()


@app.post("/api/plex/unlink")
def unlink_plex(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    linker.unlink()
    return linker.status()


@app.post("/api/admin/deploy", status_code=501)
def deploy() -> dict:
    """Stub — real `git pull` logic lands in Stage 6, gated by the Stage 7
    settings panel's hidden long-press control. Exists now so Stages 4/6/7
    have a stable route to wire against."""
    return {"detail": "not implemented until Stage 6"}
