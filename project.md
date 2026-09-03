# The Family Downloader — Project Plan

A self-hosted pipeline that turns a tap on an iPad into the right movie, at the right quality, sitting in qBittorrent — with every torrenting decision made and hidden in the backend. The family browses TMDB-style, taps "Add to Plex," and never sees a torrent name.

This file is the persistent source of truth across stages and chat sessions. Each stage should be worked in its own chat; **read this whole file at the start of that chat**, and **append to the Decision Log below at the end of it** before closing out. The plan sections (target environment, principles, stages) should only change when a decision genuinely changes the design — not on every session.

Full staged proposal with design rationale: https://claude.ai/code/artifact/bcbb1dfc-40da-4c78-a94e-fe74194f7dd7

---

## Status

- **Current stage:** Stage 1 complete — ready to begin Stage 2.
- **Deployed:** nothing yet.
- **Last updated:** 2026-09-04.

---

## Target environment

- **Host:** TrueNAS SCALE, deployed as a Custom App (Docker Compose–based). Custom App YAML supports both `image:` (pull) and `build:`/`dockerfile_inline` (build on the NAS from source).
- **Torrent client:** qBittorrent (v4.x/v5.x), WebUI enabled, already running as its own existing TrueNAS app — not deployed by this project.
- **Search mechanism:** qBittorrent's internal Search Plugins, driven asynchronously via `/api/v2/search/start`, `/status`, `/results`, `/delete`, using the `qbittorrent-api` Python library's `search.start()` job object.
- **Metadata/browse source:** TMDB API — search, discover-by-watch-provider, popular/trending, alternate titles.
- **Client device:** iPad, Safari, touch-first, "Add to Home Screen" standalone mode.
- **Repo:** git repo already initialized at this project root, remote `origin/main` configured.

---

## Confirmed architecture (not open for re-litigation)

- **Three logical components, one deployable app:**
  1. **Frontend container** — a single static HTML file (no framework, no build step) served by nginx, which reverse-proxies `/api/*` to the backend. Only component with a published port.
  2. **Backend container** — FastAPI automation engine. No published port; reachable only from the frontend container over the internal Compose network (`backend:8000`).
  3. **qBittorrent** — existing separate app, reached by the backend over the network.
- **Same-origin only.** All client traffic goes through the frontend's single exposed port — no CORS, no two-port setup.
- **Filter on structured API fields**, not filename regex, wherever possible (`fileSize`, `nbSeeders`). Only resolution, language, source, codec and container need filename token parsing — always whole-token, normalized matching, never substring.
- **Deploy is git-pull-based, not live-reload-on-edit.** A working copy (wherever edits happen) is fully decoupled from a deployed copy bind-mounted into each container. The deployed copy only changes via `git pull`. Backend picks it up via `uvicorn --reload`; frontend picks it up on next request since there's no build step. Rollback is `git checkout`/`git revert` on the deployed copy.
- **Self-update endpoint** (`POST /api/admin/deploy`) runs exactly `git pull`, hardcoded, no path/branch/command ever accepted as input. Gated by a hidden long-press control inside the Settings panel — not real auth, an accepted risk (see Stage 6).
- **Explicitly out of bounds:** mounting the Docker socket into the backend. Self-update means "pull my own repo," nothing more privileged.
- **Requests are serialized** through a single `asyncio.Lock` — no distributed job queue, sufficient at household scale.
- **Job/request state persists in SQLite** on a volume, surviving container restarts.
- **No family profiles in the UI.** One shared Settings panel instead of per-user identity; the audit trail tracks what was picked, not who asked.

---

## Design principles carried through every stage

- **Pipeline as a library.** Search → filter → score → select → add is plain Python with no FastAPI dependency. The API is a thin wrapper, which is what makes it CLI-testable before any web layer exists.
- **One normalizer, many uses.** The same token-normalization utility matches resolution, language, source, codec, container, and title/year relevance — not several ad hoc string checks.
- **Two passes, never one.** "Is this actually the right movie" (relevance) and "which release of it is best" (quality) are different problems, solved differently — the first stays conservative and rule-based, the second is where the rich scoring lives.
- **Fail safe, not best guess.** When a match is ambiguous, the system reports "no qualifying results" rather than confidently downloading something close-but-wrong.
- **Hidden, never unrecoverable.** The family never sees torrent names or seeder counts — but every job keeps the candidate it picked, the search variant that found it, and its score breakdown, so a wrong download can be audited after the fact.
- **Secrets never reach the browser.** TMDB key, Plex token, git deploy key — all server-side only. The frontend hotlinks TMDB's public image CDN directly; every data call goes through the backend proxy.
- **Deploy is one fixed verb.** `git pull`, hardcoded. No Docker socket, ever.
- **Same origin, one door.** nginx is the only published port.

---

## Stages

Sequencing note: the original spine (engine → API → frontend → containerize → deploy → family → hardening) held, with three deliberate changes — title resolution split out as its own stage ahead of the qBittorrent pipeline; persistence moved earlier into the API stage rather than the family layer, since restart-safe job state is a day-one requirement; and Docker Compose pulled forward into the frontend stage, so TrueNAS packaging becomes mechanical rather than exploratory.

### Stage 0 — Feasibility spike
*Throwaway. No dependencies.*

**Purpose:** Verify qBittorrent's search plugins and TMDB actually behave the way this whole plan assumes, before writing real code.

**Deliverables / done when:**
- A note on which plugins are enabled and return results; how often `nbSeeders` is `-1`/missing and `fileSize` is absent; the real `fileUrl` vs. `descrLink` ratio.
- A TMDB API key created, with a handful of real lookups tried, including one franchise title, to see alternate-title data quality.

**Risks / decisions:** If plugins are unreliable on seeders/size, filter defaults in Stage 2 need to change before they're built.

**Validation:** Whether this plan's assumptions survive contact with real data.

---

### Stage 1 — Media discovery & resolution
*Depends on: Stage 0. No qBittorrent dependency.*

**Purpose:** Everything the iPad UI browses and searches is TMDB, proxied through the backend. Owns the whole browse surface (poster grids, provider rows, search) plus resolving one selected title into a media identity and an ordered list of qBittorrent query variants.

**Deliverables / done when:**
- TMDB client wrapper: search, alternate-titles, discover-by-watch-provider, popular/trending, provider list — called only from the backend, key never reaches the browser.
- In-memory TTL response cache in front of popular/discover/provider-list calls.
- Variant generator: up to 3–4 ranked queries — canonical title, `original_title` if different, title without subtitle, title+year.
- Shared normalization/token-matching utility (reused by Stage 2 for language, source, codec, container, and title relevance).
- CLI: `resolve <tmdb-id>` prints resolved media + variant list.

**Settled:**
- TMDB over OMDB.
- Family disambiguates *which* movie (by browsing/tapping a poster); backend only ever disambiguates *which release* of that already-specific movie.
- In-memory TTL cache, not a persisted cache table — kept as the plan's default; nothing in Stage 0/1 surfaced a reason to persist it.

**Validation:** Unit tests on the normalization utility. Manual CLI/HTTP runs against real TMDB data — search, a provider's popular list, resolution for a franchise title.

**Stage 1 complete.** Implementation lives in `backend/app/`: `normalize.py` (shared token utility), `cache.py` (generic `ttl_cache` decorator), `tmdb.py` (client wrapper — search, get_movie, alternative_titles uncached; popular/trending/watch_providers/discover_by_provider TTL-cached at 300s), `resolve.py` (`MediaIdentity` + `generate_variants`), `cli.py` (`resolve <tmdb-id>`). 14 unit tests passing (`backend/tests/`). Manually verified live against TMDB: `resolve 693134` (Dune: Part Two) → 3 variants; search, alternative_titles (35 entries), popular, watch_providers, and discover_by_provider all returned data matching the Stage 0 spike's numbers exactly. TMDB key lives in gitignored `backend/.env`, loaded via `python-dotenv`, never referenced by the frontend.

---

### Stage 2 — Match, score & add pipeline
*Depends on: Stage 1, Stage 0. The core engine.*

**Purpose:** Two passes. **Pass one** (relevance gate): is this candidate actually the title Stage 1 resolved? Conservative, rule-based. **Pass two** (quality score): among everything that survives pass one, which release is best — ranked by source, codec, container and size, not just seeders?

**Deliverables / done when:**
- Search executor: `search.start()` per variant, polled via `.status()` against a 45–60s ceiling, results via `.results()`, `.delete()` always in `try/finally`.
- **Pass one:** normalized-title overlap against every Stage 1 title variant, plus year within ±1, plus resolution (must contain 2160p/4K) and language allow/blacklist — all via the shared token utility, no fuzzy-distance matching.
- **Pass two:** weighted composite score across source type (REMUX > BluRay/BDRip > WEB-DL > WEBRip > HDTV), codec (HEVC/x265 > AVC/x264), container (MKV > MP4 > other) — same token utility. File size (already bounded by a configured GB range) breaks near-ties. `nbSeeders` is a viability gate + final tiebreaker, not the primary key.
  - *Worked example (from your Dune case):* of `dune.2160p.remux.webdl.mkv` (40GB), `dune.2160p.HEVC.remux.mkv` (85GB), and `dune.2160p.h264.webdl.mp4` (34GB), the second wins — best source (remux) + best codec (HEVC) + best container (mkv), with size confirming it.
- Fallback loop across Stage 1's variants, stopping at the first that produces any gate-passing candidate.
- Dedup within a result set (normalized name+size or infohash) and against `torrents_info()` already present.
- Category ensure-exists, then add via `fileUrl` — skip `descrLink`-only results.
- CLI: `download <tmdb-id>` runs the full pipeline and prints the winner *and its score breakdown* — this is the audit trail until Stage 8.

**Settled:**
- Free space read from qBittorrent's own `server_state.free_space_on_disk` (via `sync/maindata`) — no extra bind mount into the backend.

**Open decisions:**
- [ ] Resolution stays a hard 2160p/4K gate — quality tiering applies to source/codec/container *within* it, not across resolutions (no falling back to 1080p). **This is the biggest open call in the plan.**
- [ ] Quality ranking implemented as a weighted composite score, not literal nested tier-group cascading.
- [ ] Category scheme: one bucket for everything, or a movies/TV split?
- [ ] Exact source/codec/container tier lists and point values — pin down when this stage is actually built.

**Validation:** Unit tests for the scorer against fixture result sets, including the Dune example above as a named test case, plus messy-field cases (`nbSeeders` of `-1`/`0`, missing `fileSize`, `descrLink`-only). Manual CLI runs against real qBittorrent, including one deliberately hard-to-match title.

---

### Stage 3 — Backend API & persistence
*Depends on: Stage 2, packaged as an importable library.*

**Purpose:** Wrap Stages 1–2 in FastAPI, add the SQLite job store so request state survives restarts, define the API contract the frontend builds against.

**Deliverables / done when:**
- SQLite schema: requests table (id, query text, resolved TMDB id/title, status, timestamps, result-summary fields for the audit trail); a settings table (see Stage 7).
- `POST /api/search` (TMDB candidates), `POST /api/requests` (confirm → enqueue), `GET /api/requests[/…]` (status polling), stub `POST /api/admin/deploy` (real logic in Stage 6).
- `asyncio.Lock`-guarded worker driving one request at a time through: `queued → searching → downloading | no qualifying results | failed → complete`.

**Open decisions:**
- [ ] Plain polling (default) over WebSockets/SSE for status updates.
- [ ] Boot-time recovery sweep: any row stuck in `searching`/`downloading` on restart gets marked `failed — interrupted, please retry`, rather than resumed.

**Validation:** API tests with the pipeline mocked, plus one true end-to-end manual run through the API against real qBittorrent + TMDB, including a deliberate backend restart mid-job.

---

### Stage 4 — Touch frontend & same-origin proxy
*Depends on: Stage 3's stable API contract. Single static HTML file.*

**Purpose:** A TMDB-style browsing experience, hand-built: home grid of posters, streaming-provider rows, a detail view, and one button — **Add to Plex** — the only torrenting-adjacent thing the family sees. Also proves the same-origin nginx + backend architecture from a real iPad.

**Deliverables / done when:**
- Home grid from `/api/discover`; tap a provider row to browse its popular titles.
- Detail view with full TMDB info; "Add to Plex" button posts the confirmed request (`POST /api/requests`) — no torrent details ever shown.
- Status list using the six states as distinct visuals, not one spinner.
- One HTML file — markup, CSS, JS together, no build tool, no `node_modules`. TMDB poster images hotlinked directly from TMDB's CDN; every data call goes through the backend proxy.
- Local Docker Compose (nginx + backend) proving reverse-proxy + service-name DNS works from a real iPad on the LAN.
- Web app manifest, apple-touch-icon, standalone display mode. No service worker / offline caching — stale cached job status would be actively misleading.

**Settled:**
- No framework, no build step.

**Open decisions:**
- [ ] `Cache-Control: no-cache` (or short revalidating max-age) on the served HTML, so Safari standalone mode doesn't keep serving a stale version after a deploy.

**Validation:** Manual touch-testing on a real iPad, in Safari and after "Add to Home Screen," walking browse → provider row → detail → confirm, and all six status states (seed a couple of failure/no-result cases deliberately). No automated UI suite for v1.

---

### Stage 5 — TrueNAS packaging
*Depends on: Stage 4's proven local Compose setup. Mechanical by design.*

**Purpose:** Port the already-proven local Compose stack to a TrueNAS SCALE Custom App.

**Deliverables / done when:**
- Custom App YAML: frontend (published port) + backend (`build:`, no published port, `backend:8000` DNS); qBittorrent left as-is.
- Volumes: persistent SQLite data volume; deployed-copy bind mount for backend source, distinct from any workstation path.
- `uvicorn --reload` confirmed live against the mount (edit a file directly in it, watch it reload) before git enters the picture.

**Open decisions:**
- [ ] Monorepo (default: `frontend/` + `backend/` in one repo) vs. split repos — determines whether the Custom App YAML pulls one deployed-copy mount or two.
- [ ] Verify TrueNAS SCALE's UID/GID handling on bind-mounted storage on the real NAS, don't assume from Docker Desktop.

**Validation:** App running on the NAS, frontend reachable from an iPad on the LAN, backend reaching the real qBittorrent app internally, one true end-to-end request against the NAS-hosted stack.

---

### Stage 6 — Git-based deploy & update
*Depends on: Stage 5's deployed-copy mount, proven to reload on file changes.*

**Purpose:** Replace hand-editing the deployed-copy mount with a deliberate `git pull` deploy step, plus the self-update endpoint.

**Deliverables / done when:**
- Private GitHub repo (settled) with a read-only deploy key generated for the NAS specifically, stored as a mounted secret file — never in the git-tracked tree, never visible via `docker inspect`.
- Rollback proven, not assumed: `git checkout <sha>` / `git revert` on the deployed-copy mount, confirmed to take effect.
- Real `POST /api/admin/deploy`: exactly `git pull` (backend and frontend both — no build step for either) — no parameters, ever.
- Gating: hidden long-press control, living inside the Stage 7 Settings panel.

**Settled:**
- Git remote: private GitHub repo + read-only deploy key.
- Gating: hidden long-press, no real auth. **Accepted risk:** anyone with the frontend open on the LAN can trigger a deploy; blast radius is bounded since the endpoint only ever runs a fixed `git pull`, never an arbitrary command.
- Frontend deploy is now as simple as the backend's — no build step, so `git pull` alone is the entire deploy for both.

**Open decisions:**
- [ ] Remember: `git pull` covers code changes but not new Python dependencies — a changed `requirements.txt` needs a manual image recreate via the TrueNAS UI, outside this endpoint's scope.

**Validation:** Commit a trivial change, push, long-press deploy, confirm it's live — then deliberately `git checkout` back and confirm rollback takes effect too.

---

### Stage 7 — Settings & preferences
*Depends on: Stage 3 (persistence), Stage 4 (UI to host it), Stage 6 (the endpoint the update button calls). No family profiles.*

**Purpose:** One shared settings panel behind a settings icon, for adjusting pipeline behavior and pushing a remote update — no login, no profiles.

**Deliverables / done when:**
- Settings icon opening a panel with: Stage 2's quality-tier weights/defaults, size-range filter, language allow/block lists, category mapping, and (if Stage 8 wants it) Plex connection details.
- `GET`/`PUT /api/settings` backed by a settings table in the Stage 3 SQLite database — one row, not per-profile.
- The Stage 6 remote-update trigger lives inside this panel, behind the same hidden long-press; the rest of the panel is plainly visible and editable.

**Open decisions:**
- [ ] Whole-panel visibility: anyone on the iPad can open Settings (only the update trigger stays gated) — confirm this is acceptable.
- [ ] Basic sanity validation on settings edits (e.g. size-range min < max) so a bad edit degrades to "no qualifying results" rather than breaking the pipeline.

**Validation:** Open the panel, change a preference, confirm the next request's filtering reflects it. Long-press the update control, confirm a deploy fires.

---

### Stage 8 — Hardening & observability
*Depends on: all prior stages. A pass, not new scope.*

**Purpose:** Right-sized visibility so failures don't just sit there silently.

**Deliverables / done when:**
- Structured logging to stdout (TrueNAS's own app log viewer) — one line per pipeline transition, including which variant matched and its score.
- `GET /api/admin/jobs?status=failed`, gated the same way as the deploy endpoint.
- Backend healthcheck (can it reach qBittorrent).
- Optional: push notification on failure (e.g. ntfy).
- Optional: Plex partial-scan on a request reaching `complete` — the "Add to Plex" button implies near-instant appearance, and a fresh download won't show up until something triggers a scan. Needs a Plex URL + token as a Stage 7 setting.

**Open decisions:**
- [ ] Want the push-notification-on-failure option?
- [ ] Want the automatic Plex scan-on-complete option, or leave it to Plex's own folder watching?
- [ ] Log retention — TrueNAS's default handling is probably enough; don't build a shipping pipeline.
- [ ] Start the failed-jobs view as a raw JSON endpoint; only build UI for it if it gets used often.

**Validation:** Deliberately induce failures — kill qBittorrent mid-request, request something with no plausible matches, restart the backend mid-job — confirm each is diagnosable from logs/endpoint without SSHing in.

---

## Non-goals (this pass)

- No MVP code, no scaffolding, no repo structure commitment beyond what's stated above.
- No CI/CD pipeline, no Kubernetes, no auth framework.
- No Docker socket in the backend, under any stage.
- No distributed job queue — the single `asyncio.Lock` is treated as correct at household scale throughout.

---

## Decision log

*Append one entry per stage session, newest last. This is how context survives across separate chats.*

- **2026-09-04** — Plan approved. Compartmentalization approach adopted: this file is the shared plan, each stage gets its own chat.
- **2026-09-04** — Stage 0 spike run against the real NAS-hosted qBittorrent (v5.2.3, 21 plugins enabled) and a live TMDB key. Full findings: [spikes/stage0-feasibility.md](spikes/stage0-feasibility.md). Headline results: plan's core assumptions survive, with two adjustments carried into Stage 2 — (1) plugins can return error/config rows disguised as results with a populated but bogus `fileUrl` (seen from a misconfigured `jackett` plugin pointing at its own local API) and must be excluded, not just field-validated; (2) `nbSeeders` is `-1` (unknown) on ~50% of real rows, almost entirely from one high-volume plugin (torlock), confirming seeders must stay a tiebreaker/gate, never the primary key. `fileSize` was reliable (98%+ valid). TMDB search/alternative_titles/discover-by-provider/popular all matched the assumed contract; alternate-titles data for a franchise title (Dune: Part Two) was rich (35 entries) but mostly non-Latin-script noise, confirming the plan's small-curated-variant-list approach over using every AKA.
- **2026-09-04** — Stage 1 built and verified. New `backend/` Python package (venv + `requirements.txt`: `requests`, `python-dotenv`, `pytest`) holding the pipeline-as-a-library code: `app/normalize.py` (whole-token normalizer, shared with Stage 2), `app/cache.py` (generic TTL-cache decorator), `app/tmdb.py` (TMDB client — search/get_movie/alternative_titles uncached, popular/trending/watch_providers/discover_by_provider TTL-cached at 300s), `app/resolve.py` (`MediaIdentity` dataclass + `generate_variants`, capped at 4: title, original_title if different, subtitle-free title, title+year), `app/cli.py` (`resolve <tmdb-id>`). TMDB key stored in gitignored `backend/.env`, loaded server-side only via `python-dotenv` — confirmed via repo-root `.gitignore`. 14 unit tests pass (normalization edge cases — diacritics, whole-token vs. substring like `265` inside `x265`/`1265`; variant generation for franchise/subtitle/no-subtitle/no-year cases). Manually exercised against live TMDB: `resolve 693134` (Dune: Part Two) returned 3 variants; direct client calls for search, alternative_titles (35 entries), popular, watch_providers (Netflix id 8), and discover_by_provider (4,748 total results) all matched Stage 0's numbers, and the TTL cache was confirmed not to grow on a repeat call. No deviations from the plan; the one open decision (in-memory vs. persisted cache) was closed in favor of the plan's stated default. Ready for Stage 2.

