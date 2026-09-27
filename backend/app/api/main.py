"""The FastAPI application itself: process-wide logging, the lifespan that
builds the store, clients and background worker, and the rate limiter's
wiring. Routes are registered by the area modules and the default-deny
routers included by this package's __init__."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import config
from app.db import RequestStore
from app.logging_config import configure_logging
from app.plex import LoginSession, PlexLinker
from app.qbt import QBTClient
from app.tmdb import TMDBClient
from app.worker import Worker
from app.api.deps import limiter, logger


configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = RequestStore(config.DB_PATH)
    app.state.store = store
    app.state.started_at = datetime.now(timezone.utc)
    # Settings › Plex library folders saved from the UI (env still wins).
    config.apply_library_overrides(store)

    # TMDBClient no longer raises on an empty/missing key (see tmdb.py) —
    # the app must boot, and serve the first-run setup wizard's own
    # routes, even before a key exists anywhere. Resolved once, here, at
    # startup — see get_tmdb's own docstring for why this isn't
    # re-resolved per-request (a key saved through the wizard needs a
    # restart to take effect, a named/accepted gap, not silently solved).
    app.state.tmdb = TMDBClient(config.resolve_tmdb_api_key(store) or "")

    # qBittorrent's own client logs in eagerly at construction (see
    # qbt.py) — unlike TMDB, that can't be deferred without touching the
    # third-party client library it wraps. A fresh install with qBittorrent
    # not yet configured/reachable must still boot successfully (the setup
    # wizard is exactly what configures it), so a construction failure here
    # is caught rather than left to crash startup.
    qbt_config = config.resolve_qbt_config(store)
    try:
        app.state.qbt = QBTClient(qbt_config.host, qbt_config.port, qbt_config.username, qbt_config.password)
    except Exception as exc:  # pragma: no cover — exact exception type is qbittorrentapi's, not ours to depend on
        logger.warning("qBittorrent unreachable at startup (%s) — background worker not started this boot", exc)
        app.state.qbt = None

    app.state.worker = Worker(store, app.state.tmdb, app.state.qbt) if app.state.qbt else None
    app.state.login_session = LoginSession(store)
    app.state.plex_linker = PlexLinker(store)

    # Frontend migration Part G1: the whole point of the setup token is
    # that the admin finds it by checking this container's own logs —
    # generate (or recall the already-persisted one, see
    # get_or_create_setup_token's own docstring) it and print it clearly
    # right at startup, every boot until setup actually completes. Doing
    # this lazily (only whenever the first /api/setup/* request happened
    # to land) would mean there's nowhere the admin could ever actually
    # go read it from before making that first request.
    if not store.get_settings().get("plex_server_machine_id"):
        token = store.get_or_create_setup_token()
        logger.warning("=" * 60)
        logger.warning("First-run setup needed. Setup code: %s", token)
        logger.warning("Enter this in the setup wizard to continue.")
        logger.warning("=" * 60)

    if app.state.worker:
        await app.state.worker.start()
    else:
        # Named, accepted gap (frontend migration Part E): on a fresh
        # install, qBittorrent isn't reachable yet at this first boot (the
        # setup wizard is what configures it) — the worker, and any route
        # depending on get_worker/get_qbt, stay unavailable until the next
        # restart after that's fixed. Only reachable at all before
        # qBittorrent's ever been configured; nothing could have been
        # queued yet for the worker to act on regardless.
        logger.warning("worker not started: qBittorrent was not reachable at boot")
    try:
        yield
    finally:
        if app.state.worker:
            await app.state.worker.stop()
        store.close()


app = FastAPI(title="Obsidian", lifespan=lifespan)

# See deps.py's `limiter` for which routes are rate limited, and why.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
