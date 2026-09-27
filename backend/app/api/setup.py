"""The first-run setup wizard's unauthenticated, setup-token-gated routes,
and the TMDB/qBittorrent helpers Settings' Connections panel reuses."""

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import config
from app.db import RequestStore
from app.qbt import QBTClient
from app.api.deps import get_store, limiter, require_setup_token
from app.api.main import app


# ---------------------------------------------------------------------------
# First-run setup wizard (frontend migration Part E) — TMDB key and
# qBittorrent connection, collected through the UI so a non-technical
# homelab install never needs to hand-edit .env. An env var always wins
# when set (existing installs, including this app's own NAS deploy, need
# zero changes); otherwise the value lives in the settings table, editable
# here during bootstrap. Every mutating route requires require_setup_token
# and 410s once setup is complete — ongoing post-setup editing is a
# separate, always-admin-gated surface (Settings' Connections panel, a
# later step of the migration, not built yet).
# ---------------------------------------------------------------------------


@app.get("/api/setup/status")
def setup_status(store: RequestStore = Depends(get_store)) -> dict:
    """No auth at all, deliberately — this is what the frontend calls
    *before* deciding whether to show the setup wizard or the login
    screen, so it can't itself be gated by either. Reveals only
    configuredness, never a secret value."""
    settings = store.get_settings()
    qbt_source = config.qbt_config_source(store)
    qbt_configured = qbt_source == "env" or bool(settings.get("qbt_host"))
    # Two distinct signals, not one — see require_admin_or_setup_bootstrap's
    # docstring: `plex_token` is set as soon as PIN sign-in succeeds (the
    # wizard uses `plex_account_linked` to know it can move on to showing
    # the server picker), but `setup_complete`/every auth gate elsewhere
    # keys specifically on a server having actually been *selected*
    # (`plex_server_machine_id`) — the step that comes after, and the one
    # that genuinely finishes bootstrap.
    plex_account_linked = bool(settings.get("plex_token"))
    setup_complete = bool(settings.get("plex_server_machine_id"))
    return {
        "tmdb_configured": bool(config.resolve_tmdb_api_key(store)),
        "tmdb_source": config.tmdb_api_key_source(store),
        "qbt_configured": qbt_configured,
        "qbt_source": qbt_source,
        "plex_account_linked": plex_account_linked,
        "plex_linked": setup_complete,
        "setup_complete": setup_complete,
    }


class SetupTmdbRequest(BaseModel):
    api_key: str = Field(min_length=1)


def _update_tmdb_key(body: SetupTmdbRequest, store: RequestStore) -> dict:
    """Saves, but doesn't immediately apply — get_tmdb's own docstring in
    app/api/deps.py explains why: it's resolved once at startup, not
    per-request. `restart_required: true` is what the caller's UI uses to
    tell the admin so, rather than implying this is live right away.
    Shared by the one-time setup route and Settings' ongoing Connections
    panel (settings.py) — identical business rule, only the auth gate differs
    (setup token pre-bootstrap, admin session after), so each stays its
    own thin route rather than duplicating this logic twice."""
    if config.tmdb_api_key_source(store) == "env":
        raise HTTPException(status_code=409, detail="TMDB API key is already configured via environment variable")
    store.update_settings({"tmdb_api_key": body.api_key})
    return {"tmdb_configured": True, "tmdb_source": "db", "restart_required": True}


@app.put("/api/setup/tmdb", dependencies=[Depends(require_setup_token)])
@limiter.limit("20/minute")
def setup_tmdb(request: Request, body: SetupTmdbRequest, store: RequestStore = Depends(get_store)) -> dict:
    return _update_tmdb_key(body, store)


class SetupQbtRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(gt=0, lt=65536)
    username: str = ""
    password: str = ""


def _test_qbt_connection(body: SetupQbtRequest) -> dict:
    try:
        client = QBTClient(body.host, body.port, body.username, body.password)
    except Exception as exc:
        return {"reachable": False, "detail": str(exc)}
    return {"reachable": client.ping()}


@app.post("/api/setup/qbittorrent/test", dependencies=[Depends(require_setup_token)])
@limiter.limit("20/minute")
def setup_qbt_test(request: Request, body: SetupQbtRequest) -> dict:
    return _test_qbt_connection(body)


def _update_qbt_connection(body: SetupQbtRequest, store: RequestStore) -> dict:
    """Same "saved, not immediately applied" note as _update_tmdb_key
    above, and the same shared-helper reasoning."""
    if config.qbt_config_source(store) == "env":
        raise HTTPException(
            status_code=409, detail="qBittorrent connection is already configured via environment variables"
        )
    store.update_settings(
        {"qbt_host": body.host, "qbt_port": body.port, "qbt_username": body.username, "qbt_password": body.password}
    )
    return {"qbt_configured": True, "qbt_source": "db", "restart_required": True}


@app.put("/api/setup/qbittorrent", dependencies=[Depends(require_setup_token)])
@limiter.limit("20/minute")
def setup_qbt(request: Request, body: SetupQbtRequest, store: RequestStore = Depends(get_store)) -> dict:
    return _update_qbt_connection(body, store)
