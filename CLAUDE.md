# CLAUDE.md

Obsidian is a self-hosted media manager. People browse TMDB-style, tap a title, and the
backend searches qBittorrent, picks a release, downloads it and files it into Plex. It runs
as a TrueNAS SCALE app, either on the owner's NAS or on a preloaded "box" sold to
non-technical buyers.

Read before large changes:
- `project.md`: the current picture (architecture, design principles, non-goals).
- `README.md`: installing, configuring, releases.
- `docs/history/project-plan.md`: the frozen build plan and decision log. When a comment
  cites "Stage N", it means this file.

## Commands

```bash
# Backend (Python 3.11): run from backend/
python -m pytest -q                      # whole suite; no network, every service is faked
python -m pytest -q tests/test_worker.py # one module's tests

# Frontend: run from frontend/
npm run lint
npm run build                            # tsc -b && vite build, the type check CI runs
```

- CI (`.github/workflows/release.yml`) runs the backend tests, then the frontend lint and
  build. It installs real `ffmpeg` and `mkvtoolnix`, because the organiser and logo tests
  drive them.
- Dev servers: use `.claude/launch.json` (`frontend-dev` on 5173, `backend-dev` on 8000).
- The suite is slow to import, so write pytest output to a file rather than piping it
  through `tail`. Piping loses the failure detail.

## Architecture map

Three parts:
1. **nginx + React frontend.** The only published port.
2. **FastAPI backend + background worker.** Reachable only as `backend:8000`.
3. **qBittorrent.**

State is SQLite. Every browser request is same-origin through `frontend/nginx.conf`.

### Backend: `backend/app/`

Every module belongs to exactly one of these areas, listed lowest first. An area may import
only from itself and the areas above it in this table (Recommendations imports nothing).
`tests/test_architecture.py` enforces this: it fails on a module with no area, an import that
reaches downwards, or a web framework imported outside `api/`. When it fails, move the code;
don't loosen the rule. A new module goes in the test's `AREAS` table and in this map.

| Area | Modules | Owns |
|---|---|---|
| **Core** | `core/`: `config.py`, `cache.py`, `logging_config.py`, `version.py` | env and defaults, TTL cache, logging, the running version |
| **Store** (`db`) | `db/` | the SQLite store: `RequestStore` (`store.py`) built from one mixin per group of tables, plus `rows.py` and `schema.py` |
| **Catalogue** | `catalogue/`: `tmdb.py`, `tvmaze.py`, `language.py`, `logos.py`, `trailers.py` | metadata, artwork, trailers |
| **Recommendations** | `recommendations/`: `feed.py`, `taste.py`, `moods.py` | home-page rows. Pure logic: imports nothing from the app |
| **qBittorrent** | `qbittorrent/client.py` | the qBittorrent WebUI client |
| **Release matching** (`matching`) | `matching/`: `normalize.py`, `resolve.py`, `tv_resolve.py`, `score.py`, `tv_score.py`, `pack_score.py`, `pipeline_settings.py` | title → search queries, then "right title?" and "best release?" rules |
| **Updater** | `updater/`: `updates.py`, `deploy.py`, `channel.py` | the two update paths, and `channel.py`: the request files the NAS updater reads |
| **Box** | `box/`: `drives.py`, `drive_recovery.py`, `plex.py`, `address.py`, `vpn.py` | box-only features |
| **Plex** | `plex.py` | Plex sign-in, library lookups, refresh and audio selection |
| **Library** | `media_organizer.py`, `reconcile.py` | filing finished downloads, cleaning up source torrents |
| **Downloads** | `pipeline.py`, `worker.py`, `tv_settings.py` | search → filter → score → add, and the background loops that drive requests. No FastAPI imports |
| **HTTP** | `api/` | one module per area. The only place auth is enforced |
| **CLI** | `cli.py` | recovery commands (`python -m app.cli --help`). Never imports `api/` |

- **The HTTP layer, `api/`:**
  - `main.py` builds the app and its lifespan (store, clients, worker).
  - `deps.py` holds sessions, the `require_*` gates and the two default-deny routers.
  - `schemas.py` holds response models shared by more than one area.
  - `helpers.py` holds the "On Plex" annotation.
  - Every other module is one area of routes.
- **`truenas/`:** runs on the NAS host, not in the container.
  - `obsidian-updater.py` is the root cron updater.
  - `provision.py` sets up a box at the factory.
  - The compose files and `card.html` (the printed setup card) also live here.
- **Tests:** `backend/tests/` has one `test_<module>.py` per module. `test_api.py` holds the
  boundary fakes (`FakeTMDBClient`, `FakeQBTClient`, `FakePlexLinker`) and the client
  fixtures. Reuse them; don't write new fakes.

### Frontend: `frontend/src/`

| Folder | Holds |
|---|---|
| `app/` | the shell: `AppShell`, top bar, tab bar, toasts, `routes.tsx` (most pages are lazy chunks) |
| `features/<area>/` | everything one page or area needs: components, its CSS, hooks used only there |
| `components/ui/` | design-system primitives (Icon, Img, Modal, Toggle, Skeleton, StatusPill…) |
| `components/media/` | poster cards, rows, grids and the hero, used across pages |
| `lib/` | hooks and helpers used by more than one feature |
| `api/` | typed fetch wrappers, one file per backend area, built on `api/client.ts` |
| `types/` | API response types, one file per area |
| `styles/` | `tokens.css` (the design system), `global.css`, `skeleton.css` |

## Where new code goes

- **New backend route.**
  - Put it in the matching `api/<area>.py` and register it on `router` or `admin_router`
    from `deps.py`.
  - Make a new `api/` module only for a genuinely new area. Import it in `api/__init__.py`,
    where the import order is load-bearing (see the comment there).
- **New background job.** Don't add it to `worker.py`. Write it as functions in its own
  module (as `reconcile.py` and `box/drive_recovery.py` are), and keep only the loop that calls
  it in the worker.
- **New table or column.**
  - Change the schema and add an in-place migration in `db/schema.py`.
  - Add the row dataclass to `db/rows.py`, and the store methods to the `db/` mixin for
    that group of tables (or a new mixin, added to `RequestStore` in `db/store.py`).
  - Import from `app.db`, never from the modules inside it. Never write SQL outside the
    store.
- **New setting.**
  - If it's editable in the app, follow the `matching/pipeline_settings.py` / `tv_settings.py`
    pattern: a dataclass resolved fresh from the store each time it's used, so edits apply
    with no restart.
  - If it comes from the environment, put it in `core/config.py`.
- **New page.** Make a `features/<area>/` folder and add a lazy route in `app/routes.tsx`.
  Add its API calls to `api/<area>.ts` and its types to `types/<area>.ts`.
- **Shared frontend code.** Code used by one feature stays in that feature's folder. Move
  it to `lib/` or `components/` the moment a second feature needs it.
- **Big files.** `worker.py`, `plex.py` and `media_organizer.py` are already 1,300–2,000
  lines. Don't grow them. Add a new module next to them instead, and split by
  responsibility whenever you're already making a large change in one.

## Rules that must not break

- **Auth is default-deny.** Routes go on `router` / `admin_router`, which carry the auth
  dependency. Only `/api/health`, `/api/auth/login/*` and `/api/setup/*` sit directly on
  `app`, and `/api/setup/*` is gated by the setup token.
- **The backend never publishes a port.** The LAN gate, security headers and rate limits
  live in `frontend/nginx.conf` in front of it.
- **Secrets never reach the browser.** That covers the TMDB key, Plex tokens, qBittorrent
  credentials and the VPN details. No response returns one.
- **No Docker socket and no TrueNAS credential in the container, ever.**
  - Anything that changes the host is left as a request file in the data folder
    (`data/updater/*.json`) through `updater/channel.py`.
  - `truenas/obsidian-updater.py` applies it as root.
- **Box features are gated by env vars:** `MEDIA_DRIVES_ROOT`, `GLUETUN_URL`,
  `PLEX_LOCAL_URL`, `SETUP_CODE`.
  - A non-box install must behave exactly as before.
  - New box behaviour needs the same kind of gate.
- **qBittorrent never touches the internet without the VPN on a box.** Don't change
  `network_mode: service:gluetun` or the `tun0` binding in `truenas/obsidian-box.yaml`.
- **Release matching stays fail-safe:**
  - Use the one normaliser in `matching/normalize.py`, with whole-token matching and never
    substring matching.
  - Keep "right title?" (gate) separate from "best release?" (score).
  - When in doubt, end in "no qualifying results" rather than a close-but-wrong download.
  - Don't retune `matching/score.py`'s weights casually. They were tuned against live data.
- **Never hardcode a home-network address** (no LAN IPs as defaults).
- **Customers can't SSH in.** Changes to a Dockerfile, `nginx.conf` or `requirements.txt`
  only reach a box through a new image. Judge deploy and update changes by whether a
  non-technical owner could recover from a failure.

## Frontend conventions

- **Design spec.** `design-exploration/obsidian.html` is the spec, and `styles/tokens.css`
  mirrors its System tab. Change both together, and use tokens rather than raw colours or
  sizes. The brand kit is in `brand_identity/obsidian-brand/`.
- **CSS and lazy pages.** Most pages are lazy chunks, and a chunk's CSS loads only with it.
  CSS used by more than one page must live in an always-loaded file: `global.css`, or a
  `components/` stylesheet that Home already imports. Never put shared CSS in one page's
  stylesheet.
- **Copy style.** All UI copy is sentence case ("More like this"). Proper names keep their
  capitals (Plex, Netflix). Show TMDB genres and statuses through `genreLabel()` and
  `showStatusLabel()` in `lib/genres.ts`.
- **Data fetching.** Use TanStack Query through the `api/` wrappers. Don't call `fetch`
  directly in components.
- **Layout breakpoints.** Phone is below 640px, tablet 640–859px, desktop 860px and up.
  Every layout must work at phone width with no sideways scroll.

## Code style

- Every module opens with a docstring saying what it owns and *why* it's built that way.
  Keep that up for new modules.
- Comments explain decisions and measured facts, not what the code literally does. Match
  the density of the surrounding code.
- Python uses plain `sqlite3` (no ORM), dataclasses and `asyncio.to_thread` for blocking
  store calls.
- Tests fake services at the boundary. No test touches the network.

## Git

- Work on a branch named for the feature (`drive-recovery`, `box-address`).
- Merge into `main` with `Merge <branch>: <lowercase summary>`.
- Commit subjects are plain imperative sentences describing the effect ("Download a failed
  drive's titles again"). Avoid prefixes like `feat:`.
- Every push to `main` publishes a `beta` image. A `vX.Y.Z` tag publishes `stable`, and
  only a person cuts one.
- Add a `CHANGELOG.md` entry under `[Unreleased]` for anything a user would notice. Write it
  for the household, not developers.
