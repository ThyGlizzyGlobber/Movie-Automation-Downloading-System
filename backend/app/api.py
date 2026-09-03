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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    year: int | None = None


class CreateRequest(BaseModel):
    tmdb_id: int
    query: str | None = None


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
        data = tmdb.search_movie(body.query, year=body.year)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return data.get("results", [])


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


@app.post("/api/admin/deploy", status_code=501)
def deploy() -> dict:
    """Stub — real `git pull` logic lands in Stage 6, gated by the Stage 7
    settings panel's hidden long-press control. Exists now so Stages 4/6/7
    have a stable route to wire against."""
    return {"detail": "not implemented until Stage 6"}
