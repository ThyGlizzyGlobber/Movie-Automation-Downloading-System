# The Family Downloader — Project Plan

A self-hosted pipeline that turns a tap on an iPad into the right movie, at the right quality, sitting in qBittorrent — with every torrenting decision made and hidden in the backend. The family browses TMDB-style, taps "Add to Plex," and never sees a torrent name.

This file is the persistent source of truth across stages and chat sessions. Each stage should be worked in its own chat; **read this whole file at the start of that chat**, and **append to the Decision Log below at the end of it** before closing out. The plan sections (target environment, principles, stages) should only change when a decision genuinely changes the design — not on every session.

Full staged proposal with design rationale: https://claude.ai/code/artifact/bcbb1dfc-40da-4c78-a94e-fe74194f7dd7

---

## Status

- **Current stage:** Stage 2 complete — ready to begin Stage 3.
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
- **Pass one:** normalized-title overlap against every Stage 1 title variant, plus year within ±1, plus a resolution *floor* (see below), language allow/blacklist, and a cam/telesync/screener blocklist (`cam`, `hdcam`, `ts`, `telesync`, `screener`, `r5`, etc. — always on, unlike the language lists which default open) — all via the shared token utility, no fuzzy-distance matching. The cam blocklist only catches an *explicitly*-tagged bootleg; a real-world test on a movie 5-6 weeks past theatrical (Spider-Man: Brand New Day, 2026) found the only real risk case was releases with no source tag at all (`4K (Small File)`), which this can't catch since there's nothing to match against — noted as a known gap, not solved here.
- **Pass two:** weighted composite score, resolution (2160p/4K > 1080p > 720p > 480p) ranked above source type (REMUX > BluRay/BDRip > WEB-DL > WEBRip > HDTV), codec (HEVC/x265 > AVC/x264), seeder health (a coarse tier — e.g. 100+/30+/10+ — not raw count), and container (MKV > MP4 > other) — same token utility. Seeder health outranks container, not the reverse: container is a weak, easily-missing signal (a REMUX's container is very often left unstated since MKV is the de facto default) while a large swarm-size gap is a real practical difference — a live cross-check confirmed a well-seeded release with no stated container losing to a barely-seeded one that merely spelled its container out. `nbSeeders` is a real (if bounded) term in the composite, plus a practical viability floor before scoring even starts and a final known-then-raw-count tiebreaker (ahead of size) after — never able to override resolution, source, or codec, since Stage 0 found ~50% of real rows report it as unknown. File size (already bounded by a configured GB range) breaks any remaining near-ties.
  - *Worked example (from your Dune case):* of `dune.2160p.remux.webdl.mkv` (40GB), `dune.2160p.HEVC.remux.mkv` (85GB), and `dune.2160p.h264.webdl.mp4` (34GB), the second wins — best source (remux) + best codec (HEVC) + best container (mkv), with size confirming it. (All three sit at the same resolution tier, so resolution doesn't move this particular example — it only starts to matter once candidates at different resolutions are being compared, which is exactly the fallback case below.)
- **Resolution as a floor, not a fixed gate:** a `MIN_RESOLUTION` setting (default `2160p`, editable in Stage 7) — any candidate at or above that tier passes pass one; resolution is then the highest-weighted tier in pass two, so a 2160p release always outranks a 1080p one when both qualify, but a 1080p release is still accepted once the floor is lowered and nothing higher is available. This is what makes "prefer 4K, fall back to 1080p if nothing at 4K qualifies" work inside a single search pass, with no separate resolution-specific retry loop. A candidate with no recognized resolution token at all still fails the floor regardless of setting — fail-safe, not a guess.
- Fallback loop across Stage 1's variants, stopping at the first that produces any gate-passing candidate.
- Dedup within a result set (normalized name+size or infohash) and against `torrents_info()` already present.
- Category ensure-exists, then add via `fileUrl` — skip `descrLink`-only results.
- CLI: `download <tmdb-id>` runs the full pipeline and prints the winner *and its score breakdown* — this is the audit trail until Stage 8.

**Settled:**
- Free space read from qBittorrent's own `server_state.free_space_on_disk` (via `sync/maindata`) — no extra bind mount into the backend, and now actually wired into the pipeline as a pre-add gate (see below).

**Open decisions:**
- [x] Resolution stays a hard 2160p/4K gate — quality tiering applies to source/codec/container *within* it, not across resolutions (no falling back to 1080p). **This was the biggest open call in the plan — reopened and resolved differently mid-Stage-2**, after the user asked whether 1080p fallback would ever be a frontend setting. Landed on: resolution becomes a *floor* setting (`MIN_RESOLUTION`, default `2160p` — so today's default behavior is unchanged) plus the top-weighted pass-two tier, rather than a fixed pass-one token check. See the "Resolution as a floor" deliverable above and `backend/app/score.py`.
- [x] Quality ranking implemented as a weighted composite score, not literal nested tier-group cascading. `resolution_score*10000 + source_score*1000 + codec_score*100 + seeder_score*10 + container_score*1`; known-then-raw seeder count, then size, as final tiebreakers — see `backend/app/score.py`. Reworked three times within Stage 2: first to fold resolution in as the dominant term; then to fold seeder health in as (at the time) the smallest term; then, after a real-world cross-check caught it mattering too little, swapped so seeder health outranks container instead of the reverse — see the decision log for the concrete case that drove that last change.
- [x] Category scheme: one bucket for everything. Single `"movies"` category (`config.CATEGORY`); no movies/TV split (this pipeline is movies-only).
- [x] Exact source/codec/container tier lists and point values — pinned down in `backend/app/config.py`: resolution 2160p/4K(4) > 1080p(3) > 720p(2) > 480p(1); source REMUX(5) > BluRay/BDRip(4) > WEB-DL(3) > WEBRip(2) > HDTV(1); codec HEVC/x265(2) > AVC/x264(1); container MKV(2) > MP4(1); seeder health 100+(3) > 30+(2) > 10+(1), unknown scored the same as tier 1 (viable, not punished — Stage 0's "unknown != zero" — but not rewarded above a verified swarm either). Each tier matches multiple real-world spelling variants (e.g. `WEB-DL`/`WEBDL`, `H.265`/`x265`), found by testing against Stage 0's live sample. `MIN_SEEDERS` (the pass-two viability floor) raised from 1 to 10 in the same pass — a bare "not completely dead" check didn't protect against a genuinely slow download.

**Validation:** Unit tests for the scorer against fixture result sets, including the Dune example above as a named test case, plus messy-field cases (`nbSeeders` of `-1`/`0`, missing `fileSize`, `descrLink`-only). Manual CLI runs against real qBittorrent, including one deliberately hard-to-match title.

**Stage 2 complete.** New modules in `backend/app/`: `qbt.py` (qBittorrent client wrapper — `search()` runs one `search.start()` job to the 55s ceiling with `.delete()` in `finally`, plus `existing_torrent_hashes()`, `free_space_bytes()` via `sync_maindata()`, `ensure_category()`, `add_torrent()`); `score.py` (plugin/result trust filter, pass-one relevance gate — title-variant phrase match, year ±1 tolerance, resolution floor, language allow/blocklist, cam/telesync/screener blocklist — viability gate, dedup by infohash/name+size, pass-two weighted composite scorer with resolution as the dominant tier and seeder health outranking container); `pipeline.py` (`download()` — the fallback loop across Stage 1's variants, gating the ranked winner against qBittorrent's real free space before adding, with a distinct `"insufficient free space"` status alongside `"added"`/`"no qualifying results"`). CLI gained `download <tmdb-id>`, printing the winner and its full score breakdown (now including `resolution_score` and `seeder_score`) as the audit trail. 68 unit tests passing (`backend/tests/`), including the Dune worked example from this file as a named test case, messy-field fixtures (`nbSeeders` unknown/known-zero, missing `fileSize`, jackett-style localhost `fileUrl`), and the resolution-floor/fallback behavior (floor rejects/admits by tier, ranking prefers the higher resolution when both qualify, falls back to the lower one when it's all that's available). One deviation from the plan text beyond the resolution rework: added a `free_space_bytes()` gate that wasn't explicitly listed under Stage 2's deliverables but was already "Settled" as a data source — wiring it in now (skip an over-large winner, try the next-best candidate, else the next variant) matches "fail safe, not best guess" and meant it didn't sit unused until Stage 3.

Manually verified against the real NAS (qBittorrent v5.2.3 at `192.168.0.133:30024`, confirmed reachable and unauthenticated from this network, matching Stage 0): `download 693134` (Dune: Part Two) found 123 qualifying candidates on the first variant and added `Dune Part Two 2024 2160p BluRay REMUX DV HDR ENG LATINO DDP5.1 H265 MP4-BEN THE MEN` (65.8GB, torlock, `nbSeeders` unknown) — resolution 4/source 5/codec 2/container 1. This was a real add to the household's qBittorrent (confirmed with the user before leaving it running rather than deleting it) — worth remembering for future stages' manual validation runs, which will keep triggering real downloads unless a disposable/obscure title is used instead. `download 895` (Andrei Rublev, 1966 — deliberately hard-to-match, no plausible 2160p release) correctly fell through both of its variants and returned `"no qualifying results"` at the default floor. The resolution-floor rework was then verified read-only (gate/scoring logic run against real search results, without calling `add_torrent`) against that same Andrei Rublev search with the floor temporarily lowered: 33 of 77 raw results qualified at a `720p` floor, top-ranked `Andrei Rublev (1966) Criterion (1080p BluRay x265 HEVC 10bit AAC 1 0 Russian Tigole)` — confirming the floor+tier logic parses real 1080p release names correctly without needing another live add to prove it. No deviations from the plan otherwise. Ready for Stage 3.

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
- Settings icon opening a panel with: Stage 2's quality-tier weights/defaults, **minimum resolution floor (default 2160p/4K — lowering it to 1080p is what enables falling back to a lower resolution when nothing at 4K qualifies)**, size-range filter, language allow/block lists, category mapping, and (if Stage 8 wants it) Plex connection details.
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
- **2026-09-04** — Stage 2 built and verified. `requirements.txt` gained `qbittorrent-api`; `app/config.py` gained qBittorrent connection settings (defaulting to the real NAS instance from Stage 0, `192.168.0.133:30024`, no WebUI auth needed on this network — confirmed again live) and the pipeline defaults (category, resolution/year/language gate settings, source/codec/container tier tables, size range, min seeders, plugin distrust list) as plain module constants ahead of Stage 7's settings table. New: `app/qbt.py`, `app/score.py`, `app/pipeline.py` — see the Stage 2 section above for what each does and all four of its open decisions being settled. 51 unit tests pass, all four Stage 2 open decisions closed as described above, and two manual CLI runs confirmed the pipeline end-to-end against the real NAS: `download 693134` (Dune: Part Two) matched, scored, and added a real torrent — a genuine side effect flagged to and kept by the user rather than deleted — and `download 895` (Andrei Rublev, deliberately unmatchable at 4K) correctly returned `"no qualifying results"` after exhausting its variants. One addition beyond the plan's literal deliverables list: a free-space gate before adding, using the `free_space_on_disk` read that Stage 2 had already "Settled" as a data source but the deliverables didn't explicitly wire in — added now rather than left dangling, with a new `"insufficient free space"` status distinct from `"no qualifying results"`.
- **2026-09-04** — Same session, before moving to Stage 3: the user asked whether 1080p would ever be a frontend setting with automatic fallback when no 4K result is found — reopening "the biggest open call in the plan" (the hard 2160p/4K gate), which Stage 2 had just built and live-tested around. Agreed to fold it into the plan immediately rather than defer to Stage 7. Reworked `backend/app/config.py`/`score.py`/`pipeline.py`: resolution moved from a fixed pass-one token check to a `RESOLUTION_TIERS` table (2160p/4K(4) > 1080p(3) > 720p(2) > 480p(1)) plus a `MIN_RESOLUTION` floor setting (default `"2160p"`, so current behavior is unchanged until someone lowers it), with resolution folded into pass two's composite as the highest-weighted term (`resolution_score*1000 + source_score*100 + codec_score*10 + container_score*1`) so a qualifying 2160p candidate always outranks a qualifying 1080p one, but a 1080p candidate is still picked when it's all that's available under a lowered floor — no separate fallback search pass needed, it falls out of one ranked pool per variant. Added `resolution_score` to `Score` and the CLI's audit-trail printout. 10 new unit tests (`backend/tests/test_score.py`, `test_pipeline.py`) cover the floor rejecting/admitting by tier, an unrecognized-resolution candidate always failing regardless of floor, ranking preferring the higher resolution when both qualify, and the pipeline actually adding a 1080p candidate once the floor is lowered and nothing higher exists — 61 total tests passing. Validated read-only against real data rather than triggering another live add: re-ran the Andrei Rublev (895) search from the earlier manual test with the floor temporarily lowered to `720p` (in-process, no `add_torrent` call) — 33 of 77 raw results qualified, correctly top-ranking a real `1080p BluRay x265 HEVC` release. Stage 7's settings list updated to name the resolution floor explicitly. Ready for Stage 3.
- **2026-09-04** — Same session, immediately after: the user asked whether seed count influences which torrent gets picked, worried a barely-seeded release would "take forever." It didn't, meaningfully — `nbSeeders` was a pure tiebreaker after composite score and file size were already equal, which real quality-tier differences almost never allow, so a well-seeded lower-tier release could easily lose to a 3-seeder higher-tier one. Fixed at two levels in `backend/app/config.py`/`score.py`: raised `MIN_SEEDERS` (the pass-two viability floor) from 1 to 10, using the user's own "only 10 seeders" example as the cutoff below which a known count is excluded outright (unknown, per Stage 0, still always passes); and added `SEEDER_TIERS` (100+/30+/10+ seeders) as a genuine — but deliberately smallest — term in the composite (`resolution*10000 + source*1000 + codec*100 + container*10 + seeder_tier*1`), so a much healthier swarm can now edge out an equally-tiered but barely-alive one without letting seed count override an actual quality difference (kept deliberately weak, matching the plan's original "never the primary key" stance and Stage 0's finding that ~50% of real rows report seeders as unknown). Unknown seeders score the same as a candidate just clearing the floor — not punished, not rewarded. Added `seeder_score` to `Score` and the CLI breakdown. 5 new unit tests, plus one updated fixture whose seed count (5) fell below the new floor — 66 total tests passing. Validated read-only against a real live Dune: Part Two search (no `add_torrent` call): 130 relevant candidates, 107 cleared the new 10-seed viability floor; also surfaced what looked at first like a scoring quirk — a release tagged `...MP4-BEN THE MEN` nudging ahead of an equally-tiered, far-better-seeded (267 vs ~10) candidate. Initially guessed this was a false-positive container match against a scene-group name and left it as-is; turned out to be wrong and worth a real fix — see the next entry.
- **2026-09-04** — Same session, continuing the real-world testing the user asked for: ran `download 693134` (Dune: Part Two) live again to get a fresh pick to cross-check by hand against the user's own qBittorrent search UI. The user's manual pick (`...REMUX-FraMeSToR`, 267 seeders, no stated container) was exactly the candidate the pipeline had ranked *second*, behind the three `MP4`-tagged ~65.8GB releases at 10-11 seeders (and one at unknown). Investigating why closed out the previous entry's "false positive" guess as actually wrong: `H265.MP4-BTM` is the full, untruncated name (the earlier read was cut off mid-groupname) — `MP4` there is a real container tag in standard `TAGS.CONTAINER-GROUP` scene convention, not a coincidental match. The real issue was two compounding weight/tiebreak problems: (1) container was weighted `×10` vs. seeder health's `×1`, so one real container point could outweigh the *entire* possible seeder-tier gap (max 2 points) — REMUX releases very often leave container unstated since MKV is the de facto default (doubly true here: TrueHD/Atmos audio barely fits in MP4 at all), so "no container token" was being scored as *worse* than a stated-but-more-questionable MP4; (2) the tiebreak order was `(composite, size_bytes, seeders_known, seeders)` — file size (a near-noise-level ~0.07% difference between postings of the same release, likely just a bundled sample file) was deciding ahead of "do we even know this swarm is alive." Fixed both in `backend/app/config.py`/`score.py`: swapped seeder health and container in the weight ladder (now `resolution*10000 + source*1000 + codec*100 + seeder*10 + container*1`, still with codec-and-above never overridable by seeder health, per the domination-margin design from the very first Stage 2 pass), and reordered the tiebreak to `(composite, seeders_known, seeders, size_bytes)`. Added 2 regression tests built directly from this exact incident (real filenames, real byte counts) — 68 total tests passing, all pre-existing tests unaffected (confirmed by hand which ones could have been, since none of them varied container independent of source/codec). Re-ran the same dry-run read-only check: FraMeSToR now ranks first (composite 45230 vs. 45211 for the `MP4` releases) — then ran `download 693134` live once more, and it added exactly `Dune.Part.Two.2024.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HEVC.REMUX-FraMeSToR`, matching the user's own manual pick precisely. The user had already cancelled the two earlier (pre-fix) Dune: Part Two downloads themselves before this run, so no cleanup was needed. This is the clearest end-to-end validation Stage 2 has had — a real independent human cross-check against real qBittorrent data, not just a plausibility check. Ready for Stage 3.
- **2026-09-04** — Same session: two more real `download` runs at the user's request, spanning years — `Deadpool` (2016, id 293660) and `Spider-Man: Brand New Day` (2026, id 969681, ~5-6 weeks past theatrical as of this session's date). Deadpool added an ideal pick (`...UHD.BluRay.REMUX.2160p.HEVC.HDR10.Atmos.TrueHD7.1-DreamHD`, 41GB, 33 seeders) — a well-established catalog title behaves exactly as hoped. Spider-Man surfaced a real gap instead: only 4 candidates passed pass one at all, all named generically (`4K (Small File)`/`4K (Large File)`, no source/codec/container tags whatsoever) — because no real BluRay/WEB-DL/REMUX release exists yet this soon after theatrical, so the resolution gate was passing whatever claimed "4K" with nothing to verify it against, which is exactly the profile of an early cam/upscale rip. Added a `CAM_BLOCKLIST` pass-one filter (`backend/app/config.py`/`score.py`) — `cam`, `hdcam`, `ts`, `telesync`, `screener`, `r5`, etc., always on (unlike the language lists, which default open) — that rejects an *explicitly*-tagged bootleg outright. Confirmed via unit test that `ts` (telesync) doesn't false-positive against the common `DTS`/`DTS-HD` audio tag (tokenizes as one word, never splits). Explicitly does **not** solve the Spider-Man case itself — those releases have no source tag to match against at all, so nothing currently distinguishes "genuinely unlabeled early release" from "bootleg with the label stripped." Left as a known, named gap rather than papered over — flagged to the user as such. 6 new unit tests, 74 total passing. Confirmed live (read-only) that Spider-Man's candidate set is unaffected (as predicted, none of its 4 candidates were explicitly cam-tagged) — no regression.
- **2026-09-04** — Same session, closing out: the user asked whether/how TV shows and weekly-releasing episodes (e.g. add "Lanterns" (2026) with only 3 episodes aired, auto-grab episode 4 when it airs) would work. Answer: not at all right now, and it's not a small gap — `tmdb.py` only calls `/movie/*` endpoints, `resolve.py` has no season/episode concept, and more fundamentally the entire pipeline (`download()`) is a one-shot resolve→search→score→add operation with no persistent "job" that outlives a single request. Auto-downloading future episodes needs a standing subscription, not a request — a new persisted job type that doesn't terminate, something to re-check it on a schedule, and per-episode dedup/state tracking, on top of TV-specific resolution and episode-aware search/matching. Agreed this is a real future stage (or stages) rather than a tweak, and needs its own dedicated planning session rather than being designed inline here — see the prompt handed to the user for that session below. Not added to the stage list yet; TV remains explicitly out of scope until that planning session produces a concrete design to fold in. Ready for Stage 3 (movies).

**Prompt handed to the user, for a fresh planning chat, to design TV show/episode-tracking/auto-download as new stage(s):**

> I want to plan out a new capability for the Family Downloader project: TV show discovery, episode-level resolution, and automatic downloading of newly-released episodes for shows the family is already watching — e.g. add "Lanterns" (2026) when only 3 episodes have aired, and have episode 4 auto-download the day it's available, without anyone re-opening the app.
>
> Read the full project plan first: `project.md` at the repo root — it's the persistent source of truth across every stage's chat, architecture decisions and design principles included. The movie pipeline (Stages 0-2) is built and working; TV was explicitly scoped out as "movies-only" when Stage 2 closed, and needs to be designed in properly now, not bolted on.
>
> Design this as one or more new stages, matching the plan's existing format (Purpose / Deliverables / Settled / Open decisions / Validation) and inserted at the right point in the sequence. At minimum, work through:
> - TV discovery & episode resolution (TMDB's `/tv` endpoints, season/episode metadata) — the Stage 1 equivalent for shows.
> - Episode-aware search & matching — season-pack vs. per-episode queries, `S01E03`-style token matching in pass one, reusing the existing scorer where it still applies.
> - The core architectural gap: everything built so far is a one-shot request (resolve → search → score → add → done). A "watch this show, grab new episodes automatically" feature needs a standing subscription that outlives a single request — persisted job state that doesn't terminate, plus something that re-checks it on a schedule (how often? per-show, or global?).
> - Per-episode dedup/state tracking, so a re-check doesn't re-search or re-add episodes already grabbed.
> - What the frontend needs to expose this (an "Add show" + ongoing "Watching" status, distinct from movies' one-shot "Add to Plex").
> - Whether/how this changes any "confirmed architecture" decisions already locked in (single `asyncio.Lock` request queue, SQLite persistence, no distributed job queue) now that background/scheduled work exists, not just synchronous requests.
>
> Append the outcome to `project.md`'s stage list and decision log the same way every other stage session has.

