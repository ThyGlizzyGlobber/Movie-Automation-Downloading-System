"""FastAPI wrapper around Stages 1-2's library code, plus the SQLite job
store. Same-origin only (confirmed architecture) — no CORS middleware; the
frontend (Stage 4) reaches this only via nginx's reverse proxy on the same
origin. TMDB key and qBittorrent credentials never reach the browser: every
route here is either a thin TMDB proxy or reads/writes the local job store.

Frontend migration Part C: every route is protected by default, not
opt-in — `router`/`admin_router` (deps.py) carry a blanket `Depends(...)`
rather than each route adding its own, specifically so a route added later
can't silently ship unauthenticated by a forgotten decorator. The small,
explicit allowlist that stays directly on `app` (unauthenticated) is
`/api/health`, `/api/auth/login/*`, and `/api/setup/*` (which has its own
token-based gate, not none at all — see require_setup_token).

`app.api:app` is the ASGI entry point. The application is built in main.py;
each area's routes live in their own module and register themselves on
import, onto `app` directly or onto the shared routers from deps.py."""

from app.api.deps import admin_router, router
from app.api.main import app

# Imported for their routes. Starlette matches in registration order, and
# a module's routes register when it is first imported, so this order is
# load-bearing: discover must come before titles, or /api/tv/{tmdb_id}
# would claim GET /api/tv/discover first.
from app.api import (  # noqa: F401
    discover,
    titles,
    hero,
    requests,
    shows,
    plex,
    auth,
    setup,
    settings,
    household,
    recommendations,
    system,
)

# Wires the default-deny routers onto the app — see this package's
# docstring for the small, explicit allowlist that stays directly on
# `app` instead (unauthenticated, or with its own bespoke gate).
app.include_router(router)
app.include_router(admin_router)
