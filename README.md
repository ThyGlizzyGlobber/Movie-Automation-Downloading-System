<div align="center">

<img src="brand_identity/obsidian-brand/icons/icon-1024.png" width="96" alt="Obsidian">

# Obsidian

**Automated Media Downloading Made Simple**

Tap a poster. The right file lands in Plex. Nobody sees a torrent.

[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/frontend-React%2019-61dafb?logo=react&logoColor=black)](https://react.dev/)
[![Python](https://img.shields.io/badge/python-3.11-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/deploy-Docker%20Compose-2496ed?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Plex](https://img.shields.io/badge/library-Plex-e5a00d?logo=plex&logoColor=black)](https://www.plex.tv/)

</div>

---

## What it is

Obsidian is a self-hosted request-and-acquire front end for a Plex household. Someone
browses a Netflix-style catalogue — on their phone, or full width on a desktop — hits
**Request**, and the file shows up in Plex: correctly named, correctly foldered, at a
sensible quality.

Every torrenting decision happens in the backend and stays there. The people using it
never see a release name, a tracker, a seeder count, or a quality tier. That is the whole
design goal: **the household gets a streaming app; the admin gets an automation pipeline.**

> [!NOTE]
> This is a personal, single-household project built for one TrueNAS SCALE box. It is
> shared in the hope it is useful, but it is not a turnkey product — expect to read some
> configuration. See [Status and scope](#status-and-scope).

<!-- SCREENSHOT: hero shot of the Home page on desktop. Suggested path: docs/screenshots/home.png -->
<!-- Optional companion: the same page on a phone, docs/screenshots/home-mobile.png -->
<!-- ![Obsidian home](docs/screenshots/home.png) -->

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Docker Compose](#option-a--docker-compose)
  - [TrueNAS SCALE](#option-b--truenas-scale-custom-app)
  - [TrueNAS SCALE, from release images](#option-c--truenas-scale-from-release-images)
- [First-run setup](#first-run-setup)
- [Configuration](#configuration)
- [Remote access](#remote-access)
- [Updating](#updating)
- [Versioning](#versioning)
- [Releases](#releases)
- [Development](#development)
- [Project structure](#project-structure)
- [Status and scope](#status-and-scope)
- [Legal](#legal)
- [License](#license)

---

## Features

### For the household

| | |
|---|---|
| **Browse like a streaming app** | TMDB-backed home, movie and TV landing pages — trending, popular, by watch provider, by person. No torrent vocabulary anywhere in the UI. |
| **Requesting is one action** | Pick a title, hit Request. Quality, release group and tracker selection are made for you and never surfaced. |
| **Knows what you already have** | Titles already in Plex are badged on the grid, so nobody requests a film that's been sitting there for a year. |
| **Follow a show** | Subscribe to a series and new episodes are fetched as they air, on a schedule, per episode. |
| **Whole seasons at once** | "Get this whole season" / "Get the complete series" as an explicit, separate action from a standing subscription. |
| **Trailers** | Played inline on detail pages. |
| **Picked for you** | Home, Movies and TV carry rows drawn from what each person has asked for, plus rotating mood rows, dealt once a day so a reload never reshuffles the page. |
| **Continue watching** | Plex's On Deck, with playback progress, straight from the home page. |
| **Built for whatever's in your hand** | A real layout pass per tier rather than one design stretched: bottom tab bar and single-column on phones, wider spacing on tablets, top-bar navigation and full-width grids from 860px up. Hover affordances only where there's a pointer. |
| **Install it like an app** | A PWA with a proper manifest and icons — installable from the browser on desktop, or "Add to Home Screen" on mobile, where it runs standalone. |

### For whoever runs it

| | |
|---|---|
| **Plex account sign-in** | No separate passwords. People sign in with Plex, and access is granted by checking they can actually see your server. The owner becomes admin automatically. |
| **Per-person permissions** | Household panel with per-user request permissions and a full audit log of sign-ins and actions. |
| **Quality pipeline you control** | Resolution floor, source/codec/container preference tiers, seeder minimums and size bounds — all adjustable from Settings, not hardcoded. |
| **Automatic file organisation** | Completed downloads are hardlinked into a Plex-shaped library layout, with embedded cover art stripped from the video stream. |
| **The right audio track** | Picks the best copy in the household's language, marks that track as the default in the file, and tells Plex to use it. |
| **Plex refresh on import** | The relevant library section is scanned — partially, when the path allows — as soon as a file lands. |
| **Storage and retention** | Disk usage visibility, request-history retention, and automatic source cleanup after import. |
| **Activity dashboard** | What's searching, downloading, stuck, or failed — with the reason. |
| **Self-update** | A "Check for updates" button runs a `git pull` and rebuilds the frontend. No Docker socket is ever mounted. |
| **Guided first run** | A setup wizard walks through the setup code, TMDB key, qBittorrent connection and Plex server link. |

<!-- SCREENSHOT: a 2x2 grid of feature shots — request flow, requests page, settings, activity dashboard. -->
<!-- Suggested paths: docs/screenshots/request.png, docs/screenshots/requests.png, docs/screenshots/settings.png, docs/screenshots/activity.png -->

---

## How it works

```mermaid
flowchart LR
    U[browser<br/>desktop / mobile] -->|HTTPS| F[frontend<br/>nginx + React]
    F -->|same-origin api proxy| B[backend<br/>FastAPI]
    B <-->|search + download| Q[(qBittorrent)]
    B <-->|metadata| T[(TMDB)]
    B <-->|library + auth| P[(Plex)]
    B --> D[(SQLite)]
    Q -->|completed files| L[media library]
    L --> P
```

The request path, end to end:

1. **Search** — the backend drives qBittorrent's own search plugins asynchronously.
2. **Score** — candidates are filtered on structured API fields (`fileSize`, `nbSeeders`)
   wherever possible, and on whole-token filename parsing only where it has to be
   (resolution, source, codec, container, language). Never substring matching.
3. **Download** — the winner is handed to qBittorrent under a managed category.
4. **Organise** — on completion the file is hardlinked into the Plex library layout and
   the source is cleaned up after a delay.
5. **Refresh** — Plex scans the affected section, and the request is marked complete.

Requests are serialised through a single `asyncio.Lock` — no distributed queue, which is
correct at household scale. All job and request state lives in SQLite on a volume and
survives restarts.

**Three components, one deployable app.** Only the frontend publishes a port; the backend
is reachable only over the internal Compose network. The LAN restriction on setup routes,
the security headers and the rate limiting all live in `frontend/nginx.conf`, *in front of*
the backend — which is why the backend container must never publish a port directly.

---

## Requirements

| | |
|---|---|
| **Docker** | with Compose v2 |
| **qBittorrent** | v4.x or v5.x, WebUI enabled, with search plugins installed. Run separately — Obsidian does not deploy it. |
| **Plex Media Server** | reachable from the backend |
| **TMDB API key** | free, from [themoviedb.org](https://www.themoviedb.org/settings/api) |
| **Shared storage** | qBittorrent's download path and Obsidian's library paths must resolve to the same files (see [Path mapping](#path-mapping)) |

---

## Installation

### Option A — Docker Compose

```bash
git clone https://github.com/ThyGlizzyGlobber/Movie-Automation-Downloading-System.git obsidian
cd obsidian
```

Create `backend/.env`:

```ini
TMDB_API_KEY=your_tmdb_key_here

QBIT_HOST=192.168.1.10
QBIT_PORT=8080
QBIT_USERNAME=admin
QBIT_PASSWORD=your_qbit_password

MOVIE_LIBRARY_ROOT=/movie-library
TV_LIBRARY_ROOT=/tv-library
```

Mount your library paths into the backend container, then:

```bash
docker compose up -d --build
```

The app is served at **http://localhost:8080**. Continue to [First-run setup](#first-run-setup).

<!-- SCREENSHOT: terminal showing a successful `docker compose up -d --build`. Suggested path: docs/screenshots/install-compose.png -->

### Option B — TrueNAS SCALE (Custom App)

`truenas/custom-app-compose.yaml` is a ready-made Custom App config. It is **not** run with
`docker compose` — paste it into the TrueNAS Custom App form (or submit it as
`custom_compose_config_string` to `app.create`).

Before applying it:

1. `git clone` this repo onto the NAS — that clone is the deployed copy, and the only thing
   the self-updater ever touches.
2. Update the paths in the YAML to match where you cloned it.
3. Inline your real `TMDB_API_KEY` — TrueNAS Custom Apps have no `env_file` support.
4. *(Optional, for self-update)* Generate a read-only GitHub deploy key, store it outside
   the git-tracked tree with `0600` permissions, and point `GIT_SSH_KEY_PATH` at it.

<!-- SCREENSHOT: the TrueNAS Custom App form with the compose config pasted in. Suggested path: docs/screenshots/install-truenas.png -->

### Option C — TrueNAS SCALE, from release images

The install a box ships with. `truenas/obsidian-release.yaml` runs the images CI publishes
to GitHub Container Registry (see [Releases](#releases)): no clone on the NAS, no deploy
key, nothing built on the box. Targets TrueNAS 25.10 (Goldeye).

1. Fill in the paths at the top of the file.
2. Paste it into **Apps → Discover Apps → Custom App → Install via YAML**.
3. Open port 8095 and go through [First-run setup](#first-run-setup). The TMDB key and
   qBittorrent connection are entered there, so the YAML holds no secrets.

It follows the `stable` channel. Change both image lines to `:beta` to follow every push
to `main` instead, or to a version (`:1.1.0`) to pin one.

### Option D — An Obsidian box

What a box ships with: [`truenas/obsidian-box.yaml`](truenas/obsidian-box.yaml), one app
holding Obsidian, qBittorrent, Plex and Gluetun. qBittorrent has no network of its own:
it reaches the internet only through the buyer's VPN, and with no VPN, nothing.
[`truenas/provision.py`](truenas/provision.py) builds a box from a fresh TrueNAS 25.10
install. A box has two internal SSDs, outside the four 3.5" bays: one TrueNAS boots from,
and one for the apps, since TrueNAS can't run apps from its boot drive. It ships with no
hard drives or with some, and each hard drive is its own storage. A download goes to the
drive with the most room and is filed on that same drive. Plex's libraries list every
drive. A drive that fails loses only its own titles.

```bash
python3 provision.py --serial OBS-0001
```

Run it as root in the TrueNAS shell, with `obsidian-box.yaml` and `obsidian-updater.py`
beside it. It:
- builds the apps SSD into the `apps` pool and has TrueNAS keep its apps there;
- names the box so it answers at http://obsidian.local, and moves TrueNAS's own page to
  port 8080;
- caps ZFS's memory cache;
- creates the datasets and permissions;
- makes the box's setup code and other secrets;
- installs the app and the updater;
- adds any hard drives in the bays, each as a drive of its own (`/media/drive1`, …), the
  same way Settings → Storage adds them later.

At the end it prints what goes on the box's card, and keeps the same in a root-only
`provisioned.json`. The card itself is [`truenas/card.html`](truenas/card.html). Open it in
a browser with the link the script prints (`card.html#OBS-0001/7KQ4-M2XP-9WTD`), choose what
the box ships with, and print. It prints as two A6 pages, front and back: how to start the
box, a QR code that opens setup with the code filled in, the code itself, what to have ready,
and how to add drives. A run that stopped
partway can simply be run again.

The buyer then opens http://obsidian.local, or scans the QR code, and the setup wizard
takes them through their setup code, a TMDB key, their VPN, and Plex.

#### Moving a git-pull install onto release images

[`truenas/switch-to-release.py`](truenas/switch-to-release.py) moves an Option B install onto
the published images in place, keeping its data folder, keys, library paths, ports and any
other services (a Cloudflare tunnel, say). It only swaps `build:` for the channel's image and
drops what a release install does without: the clone's source and built-site mounts, the
clone, the deploy key and the deploy settings. It shows what will change, setting names but
never their values, and asks first. It saves the current config to a root-only file beside
itself, and puts it back by itself if the switched app doesn't come up. In the TrueNAS
shell, from the updater's folder (see [Setting up updates](#setting-up-updates-on-a-box)):

```bash
python3 switch-to-release.py --app movie-downloader --channel beta
```

`--revert <saved file>` puts a saved config back by hand. The clone and deploy key are left
on disk; delete them once you're happy.

### Path mapping

qBittorrent and Obsidian are separate containers, so the path qBittorrent reports for a
finished download is not necessarily the path Obsidian can open. If they differ, set
`QBIT_MOVIE_SAVE_PATH` / `QBIT_TV_SAVE_PATH` to qBittorrent's view, and mount the same
storage into the backend at `MOVIE_LIBRARY_ROOT` / `TV_LIBRARY_ROOT`.

> [!IMPORTANT]
> Hardlinking only works **within one filesystem**. If downloads and library live on
> different volumes, files are copied instead — same result, more disk and more time.

---

## First-run setup

On first launch Obsidian serves a setup wizard instead of the app.

1. **Setup code** — printed in the backend log at startup. This proves you're the one
   setting the server up:
   ```bash
   docker compose logs backend | grep -i "setup code"
   ```
2. **TMDB key** — if it wasn't provided by environment variable.
3. **qBittorrent** — host, port and credentials, tested live before saving.
4. **Plex** — sign in with Plex and pick which server this install serves. Whoever
   completes this becomes the admin.

Setup routes are additionally restricted to LAN clients, so the bootstrap window cannot be
raced from the internet.

Afterwards, everyone else signs in with their own Plex account. Access is granted by
checking that the account can actually see your Plex server.

<!-- SCREENSHOT: the four setup wizard steps, in order. Suggested paths: docs/screenshots/setup-1-token.png … setup-4-plex.png -->

---

## Configuration

Most day-to-day tuning lives in **Settings** in the app. Environment variables are for
deployment-level wiring, and where both exist, **the environment wins**.

### Core

| Variable | Default | Purpose |
|---|---|---|
| `TMDB_API_KEY` | — | TMDB key. Can also be set in the wizard. |
| `QBIT_HOST` / `QBIT_PORT` | — | qBittorrent WebUI address. Also settable in the wizard. |
| `QBIT_USERNAME` / `QBIT_PASSWORD` | empty | qBittorrent WebUI credentials. |
| `MOVIE_LIBRARY_ROOT` | `/movie-library` | Where organised films are placed. |
| `TV_LIBRARY_ROOT` | `/tv-library` | Where organised episodes are placed. |
| `DB_PATH` | `backend/data/app.db` | SQLite database. Put this on a volume. |
| `PREFERRED_AUDIO_LANGUAGE` | `en` | Audio track marked as default in filed copies. Settings › Region overrides it. |

### Path translation

| Variable | Default | Purpose |
|---|---|---|
| `QBIT_MOVIE_SAVE_PATH` | unset | qBittorrent's own view of the movie download path. |
| `QBIT_TV_SAVE_PATH` | unset | qBittorrent's own view of the TV download path. |

### Timing and limits

| Variable | Default | Purpose |
|---|---|---|
| `DOWNLOAD_POLL_INTERVAL_SECONDS` | `10` | How often active downloads are polled. |
| `SOURCE_CLEANUP_DELAY_SECONDS` | `60` | Grace period before a source file is removed post-import. |
| `RETENTION_CLEANUP_INTERVAL_SECONDS` | `3600` | How often request-history retention runs. |
| `EPISODE_RECHECK_POLL_INTERVAL_SECONDS` | `900` | How often episode auto-recheck runs. |
| `TRAILER_CACHE_MAX_FILES` | `20` | Cached trailer cap. |
| `TRAILER_X_ACCEL_PREFIX` | unset | nginx `internal` location that serves cached trailers instead of uvicorn. Both compose files set `/internal-trailers/`. |
| `FFPROBE_TIMEOUT_SECONDS` | `15` | Media inspection timeout. |

### Self-update

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOY_REPO_PATH` | `/repo` | The deployed clone the updater pulls. |
| `GIT_SSH_KEY_PATH` | unset | Read-only deploy key for `git pull` over SSH. |
| `DEPLOY_TIMEOUT_SECONDS` | `60` | `git pull` timeout. |
| `DEPLOY_BUILD_TIMEOUT_SECONDS` | `180` | Frontend build timeout. |

---

## Remote access

Obsidian **never terminates HTTPS itself.** Exposing it to the internet requires a reverse
proxy in front of the frontend container. In rough order of "just works":

- **Cloudflare Tunnel** — no inbound port forward at all, and your home IP stays hidden.
  Point `cloudflared` at `http://frontend:80`; `truenas/custom-app-compose.yaml` carries the
  service, needing only a connector token from the Cloudflare dashboard.
- **Nginx Proxy Manager / Traefik** — if you already run one, add a host pointed at
  `http://frontend:80`.
- **The bundled Caddy profile** — automatic Let's Encrypt certificates, opt-in so it never
  starts on a plain `docker compose up`:
  ```bash
  PUBLIC_DOMAIN=your.domain.com docker compose --profile remote up -d
  ```
  Requires `PUBLIC_DOMAIN` to resolve to the host, with ports 80 and 443 forwarded.

> [!WARNING]
> Setup and Plex-linking are restricted to private IPs by `frontend/nginx.conf`, and a proxy
> reaches nginx from a private address of its own — so whatever you put in front of this must
> pass the real client address through, or that restriction matches the whole internet. The
> `set_real_ip_from`/`real_ip_header` pair at the top of that file's `server` block does this
> for a Cloudflare Tunnel; read the note there before exposing anything. Then check from
> outside the house that `/api/setup/status` answers **403**, not JSON.

> [!WARNING]
> Get it working over plain LAN HTTP first, confirm it on the real host, *then* set up the
> proxy. **Settings → Remote Access** is a note to yourself about where you published it,
> plus an audited record that you did — it does not affect sign-in either way.
>
> Session cookies decide `Secure` from the scheme of each request, so the LAN over HTTP and
> the tunnel over HTTPS both work at the same time. That needs the proxy to send
> `X-Forwarded-Proto`; `frontend/nginx.conf` does, and the map at the top of that file says
> what the header is and isn't worth trusting.

Whichever you choose, the same-origin `/api/` proxy inside `frontend/nginx.conf` is
untouched, so no CORS configuration is ever needed.

---

## Updating

**A release install** (Option C): **Settings → Updates.** It shows the running version and
channel, checks the registry for anything newer on that channel, shows a stable release's
notes from this changelog, and installs it with one button. Nothing needs a terminal:

1. The backend copies the database to `backups/` in the app's data folder (the newest five
   are kept), then leaves a request: the version, and the name of that copy.
2. On its next run, within a minute, the **updater** on the NAS
   ([`truenas/obsidian-updater.py`](truenas/obsidian-updater.py)) picks it up. It rewrites
   the app's image lines in TrueNAS to that exact version and has TrueNAS apply it, which
   pulls the images and restarts the app.
3. It waits up to five minutes for the new version to answer as itself, with the site
   serving. The page reloads onto the new version by itself.
4. If it doesn't come up, the updater puts the previous version back, and that version
   restores the database copy as it starts. Settings → Updates says what happened, and
   doesn't offer the failed release again; the next one published is offered as usual.

Rolling back by hand is pointing the image lines at the previous version in TrueNAS. The
copy from before each update is in `backups/`.

The app holds no TrueNAS credential and can't change which version it runs: all it can do
is leave a request naming a version, which the updater checks for itself. The updater is
the one part with that power, and it runs on the NAS, outside the app, where the app can't
reach it. The Docker socket is never mounted anywhere.

**A git-pull install** (Options A and B): **Settings → Updates → Check for updates.** That
runs a `git pull` against the deployed clone (tags included) and rebuilds the frontend in
place.

The endpoint runs exactly `git pull --ff-only --tags` — hardcoded. No path, branch or command is ever
accepted as input, and the Docker socket is never mounted. "Self-update" here means
"pull my own repo", nothing more privileged.

Rolling back is `git checkout` or `git revert` on the deployed copy.

---

## Versioning

Obsidian follows [Semantic Versioning](https://semver.org/), and the version is worked out
from git rather than kept in a file:

- A release is an annotated `vX.Y.0` tag on `main`. The tagged commit *is* that version.
- **Every commit after it is the next patch**, automatically: the commit after `v1.0.0`
  runs as `1.0.1`, the one after that `1.0.2`, and so on. Nothing is bumped by hand.
- The next tag starts the count again: `v1.1.0` for new features, `v2.0.0` for anything
  that breaks an existing install.

**Settings → About** shows the running version with its build hash and commit date, read
from the deployed clone on each check (`backend/app/version.py`). Setting `APP_VERSION` in
the backend's environment overrides it, for a build made without its `.git`.

A release install has no `.git`, so CI works the version out the same way at build time and
bakes it into the image, along with its commit, date and channel (`APP_VERSION`,
`APP_COMMIT`, `APP_BUILD_DATE`, `APP_CHANNEL`).

---

## Releases

[`.github/workflows/release.yml`](.github/workflows/release.yml) runs the backend tests and
the frontend lint and build on every pull request and push. When a push passes, it
publishes two images, `ghcr.io/thyglizzyglobber/obsidian-backend` and `obsidian-frontend`:

| You push | Channel | Image tags |
|---|---|---|
| a commit to `main` | **beta** | `beta`, the version (`1.0.37`), `sha-<commit>` |
| a `vX.Y.Z` tag on `main` | **stable** | `stable`, the version (`1.1.0`), `1.1` |

Your own box follows `beta`; a customer's follows `stable`. Nothing reaches `stable` except
a tag you push by hand, so a bad commit on `main` only ever reaches beta. A failing test
publishes nothing at all.

Cutting a release, once beta has run well:

1. Move the `[Unreleased]` notes in [`CHANGELOG.md`](CHANGELOG.md) under the new version
   and commit that.
2. Tag that commit and push the tag:

```bash
git tag -a v1.1.0 -m "Obsidian 1.1.0"
```

```bash
git push --follow-tags
```

The tag build refuses a tag that isn't on `main` or isn't shaped `vX.Y.Z`.

The images are public, like the repo. After the very first publish, check each package's
visibility under **GitHub → your profile → Packages**; a box pulls them without logging in
only if they are public.

### Setting up updates on a box

The updater is one Python script with nothing to install alongside it. It runs as root on
the NAS every minute from a TrueNAS cron job, and talks to TrueNAS the way `midclt` does,
through its local socket, so it needs no user and no API key. In the TrueNAS shell
(System → Shell):

1. Give it a folder of its own, somewhere the app doesn't mount, and fetch it there:

   ```bash
   install -d -m 700 /mnt/<pool>/obsidian-updater
   ```

   ```bash
   curl -fsSL -o /mnt/<pool>/obsidian-updater/obsidian-updater.py https://raw.githubusercontent.com/ThyGlizzyGlobber/Movie-Automation-Downloading-System/main/truenas/obsidian-updater.py
   ```

2. Install it for the app, by the name the app has in Apps → Installed:

   ```bash
   python3 /mnt/<pool>/obsidian-updater/obsidian-updater.py install --app obsidian
   ```

`install` refuses a folder the app can write to, or one holding the app's own folders: the
script runs as root, so where it lives must be out of the app's reach. It then creates the
cron job (System → Advanced → Cron Jobs, "Obsidian updater"), runs once, and prints the
updater's state. `status` prints it again later; the updater's own log is `updater.log`
beside the script.

Without it, Settings → Updates still shows what's available but says updates aren't set up
on this box. The script itself changes rarely, and doesn't update itself: fetch it again
and rerun `install` if a release ever says to.

---

## Development

```bash
# Backend
cd backend
python -m venv .venv
# Linux/macOS: source .venv/bin/activate    Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q

# Frontend
cd frontend
npm install
npm run dev        # Vite dev server
npm run build      # tsc -b && vite build
npm run lint
```

The backend test suite is pure `pytest` with no external services — TMDB, qBittorrent and
Plex are all faked at the boundary.

---

## Project structure

```
backend/
  app/
    api/                FastAPI app, one module per area; the only place auth is enforced
      main.py           app, lifespan, rate limiter
      deps.py           sessions, require_* gates, the default-deny routers
      discover.py, titles.py, hero.py, requests.py, shows.py, plex.py, auth.py,
      setup.py, settings.py, household.py, recommendations.py, system.py
    worker.py           the background loop: watch, download, organise, schedule
    pipeline.py         search → score → download
    score.py            movie release scoring (and the shared quality filters)
    tv_score.py         episode scoring
    pack_score.py       whole-season / complete-series scoring
    resolve.py          movie resolution
    tv_resolve.py       show + episode resolution
    normalize.py        the one token normaliser
    language.py         which language a title and a household speak
    media_organizer.py  post-download file placement, audio defaults, MKV rewrap
    reconcile.py        leftover-source cleanup
    taste.py, moods.py  recommendation rows
    plex.py             Plex sign-in, library lookup, refresh
    tmdb.py, tvmaze.py  metadata and browse; exact episode air times
    qbt.py              qBittorrent client
    trailers.py         trailer download and cache
    db.py               SQLite store (rows in db_rows.py, schema in db_schema.py)
    config.py           environment and defaults
    cli.py              recovery commands (python -m app.cli --help)
  tests/                pytest suite
frontend/
  src/
    app/                the shell: AppShell, top bar, tab bar, toasts, routes
    features/           one folder per area (home, movies, tv, discover, browse,
                        search, detail, person, requests, account, settings,
                        setup, auth, onboarding, recommendations)
    components/ui/      primitives: Icon, Img, Skeleton, Modal, Toggle, …
    components/media/   shared poster rows and cards: MediaRow, PosterCard, HeroCarousel, …
    lib/                hooks and helpers used by several features
    api/                typed fetch wrappers
    types/              API response types
    styles/tokens.css   the design system, single source of truth
  nginx.conf            static serving, /api proxy, image cache, security headers, LAN gate
brand_identity/         brand kit and icons
design-exploration/     obsidian.html, the visual spec the app is built to
docs/history/           the full build plan and decision log, and retired design explorations
truenas/                TrueNAS SCALE Custom App config
project.md              the short current picture: status, architecture, principles
```

---

## Status and scope

Obsidian runs in production for one household on TrueNAS SCALE. It is actively developed.
[`project.md`](project.md) is the short current picture; the full design history — every
decision and why — is in [`docs/history/project-plan.md`](docs/history/project-plan.md).

Things worth knowing before you adopt it:

- It is **built around one deployment topology** (TrueNAS SCALE + an existing qBittorrent).
  Other setups should work, but they are not what it is tested against.
- Search quality depends entirely on **your** qBittorrent search plugins. Obsidian scores
  what they return; it cannot find what they don't.
- There is **no multi-tenancy**. One household, one Plex server, one shared settings panel.

---

## Legal

Obsidian is automation software. It does not host, index, provide or bundle any content,
and it ships with no trackers, indexers or search plugins of its own — it drives a
qBittorrent instance that **you** install, configure and supply plugins for.

You are responsible for complying with the laws of your jurisdiction and with the terms of
any service you point it at. Use it for content you have the right to download.

---

## License

No license has been chosen yet, which means **all rights are reserved by default** and
others cannot legally reuse this code. If the intent is for people to use and contribute,
adding a license (MIT and AGPL-3.0 are the usual choices for self-hosted tools) should come
before promoting the repo.

---

<div align="center">

Built for the Smith household. Formerly Meridian, Smithflix, and The Family Downloader.

</div>
