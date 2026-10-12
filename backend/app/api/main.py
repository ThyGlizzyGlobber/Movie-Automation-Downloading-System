"""The FastAPI application itself: process-wide logging, the lifespan that
builds the store, clients and background worker, and the rate limiter's
wiring. Routes are registered by the area modules and the default-deny
routers included by this package's __init__."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.deps import limiter, logger
from app.catalogue.tmdb import TMDBClient
from app.core import config, version
from app.core.logging_config import configure_logging
from app.db import RequestStore
from app.plex import LoginSession, PlexLinker
from app.qbittorrent.client import QBTClient
from app.updater import updates
from app.worker import Worker


configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before the store opens: if the updater rolled a failed update back to
    # this version, put back the database copy taken before it (updates.py).
    restored = updates.restore_pending_backup(config.DB_PATH, version.current().version)
    if restored:
        logger.warning("restored the database from %s after a rolled-back update", restored)
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
        logger.warning("qBittorrent unreachable at startup (%s)", exc)
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
    if store.primary_plex_server() is None:
        token = store.get_or_create_setup_token()
        logger.warning("=" * 60)
        logger.warning("First-run setup needed. Setup code: %s", token)
        logger.warning("Enter this in the setup wizard to continue.")
        logger.warning("=" * 60)

    connecting = None
    if app.state.worker:
        await app.state.worker.start()
    else:
        # qBittorrent isn't there yet: not set up (the wizard does that), or
        # simply still starting — on a box everything powers up at once, and
        # qBittorrent waits on the VPN. Keep trying in the background rather
        # than waiting for a restart, and start the worker the moment it
        # answers, with whatever settings are saved by then.
        logger.warning("qBittorrent not reachable yet; retrying every %ss", QBT_RETRY_SECONDS)
        connecting = asyncio.create_task(_connect_qbt_when_ready(app, store), name="qbt-connect")
    try:
        yield
    finally:
        if connecting:
            connecting.cancel()
        if app.state.worker:
            await app.state.worker.stop()
        store.close()


QBT_RETRY_SECONDS = 10


async def _connect_qbt_when_ready(app: FastAPI, store: RequestStore) -> None:
    while True:
        await asyncio.sleep(QBT_RETRY_SECONDS)
        settings = config.resolve_qbt_config(store)
        try:
            qbt = await asyncio.to_thread(QBTClient, settings.host, settings.port, settings.username, settings.password)
        except Exception:  # noqa: BLE001 — not there yet; the next try will say if it still isn't
            continue
        app.state.qbt = qbt
        app.state.worker = Worker(store, app.state.tmdb, qbt)
        await app.state.worker.start()
        logger.info("qBittorrent reachable at %s:%s; background worker started", settings.host, settings.port)
        return


app = FastAPI(title="Obsidian", version=version.current().version or "unknown", lifespan=lifespan)

# See deps.py's `limiter` for which routes are rate limited, and why.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
