# Obsidian — Project Notes

**Obsidian: Automated Media Downloading Made Simple** (formerly Meridian, Smithflix, The Family Downloader)

A self-hosted pipeline that turns one tap — on a phone, a tablet or a desktop — into the right
film or episode, at the right quality, filed into Plex, with every torrenting decision made and
hidden in the backend. The household browses TMDB-style, taps "Add to Plex", and never sees a
torrent name.

This file is the short, current picture. The full build history — the Stage 0–14 plans and the
decision log, with the reasoning behind each call — is in
[`docs/history/project-plan.md`](docs/history/project-plan.md), frozen as it stood on
2026-09-20. Code comments that cite "project.md's Stage N decision log" mean that file. For how
to install, configure and run it, see the [README](README.md).

---

## Status

- Running in production for one household on TrueNAS SCALE, as a Custom App
  (`movie-downloader`), deployed from its own git clone.
- Reachable from outside the house through a Cloudflare Tunnel; see the README's Remote access
  section for why the LAN gate in `frontend/nginx.conf` depends on the real client address.
- Movies, TV (episodes, whole seasons, complete series, standing subscriptions), per-person Plex
  sign-in, recommendations and the Settings suite are all live.

## Target environment

- **Host:** TrueNAS SCALE, Docker Compose–based Custom App.
- **Torrent client:** an existing qBittorrent app (v4.x/v5.x) with the WebUI on. Search runs
  through qBittorrent's own search plugins.
- **Metadata:** TMDB, with TVmaze for exact episode air times.
- **Library:** Plex, which is also the sign-in: an account gets in by being able to see the
  linked server, and the server's owner is admin.
- **Clients:** any browser. Phone below 640px, tablet 640–859px, desktop from 860px; installable
  as a PWA.

## Architecture

- **Three components, one app.**
  1. **Frontend** — React 19 + Vite, built to `frontend/dist` and served by nginx, which also
     proxies `/api/*` to the backend and caches TMDB images under `/img/`. The only published
     port.
  2. **Backend** — FastAPI plus a background worker (search, download watch, organising, show
     schedule, rechecks). No published port; reachable only as `backend:8000` on the Compose
     network.
  3. **qBittorrent** — the existing separate app.
- **Same origin only.** Every browser request goes through nginx: no CORS, no second port. The
  LAN gate on setup routes, the security headers and the rate limiting all sit in
  `frontend/nginx.conf`, in front of the backend.
- **State in SQLite** on a volume. Requests are serialised by a single `asyncio.Lock`; no job
  queue.
- **Deploy is `git pull`, hardcoded.** "Check for updates" pulls the deployed clone and runs
  `npm ci && npm run build` in it. `backend/app` and `frontend/dist` are bind-mounted, so that is
  a deploy. Changes to a Dockerfile, `nginx.conf` or `requirements.txt` need an image rebuild on
  the NAS; the in-app update does not rebuild images. The Docker socket is never mounted.

## Design principles

- **Pipeline as a library.** Search → filter → score → select → add is plain Python with no
  FastAPI dependency; the API and the CLI are thin callers of it.
- **One normalizer.** The same whole-token normalisation matches resolution, language, source,
  codec, container, and title/year relevance. Never substring matching.
- **Two passes.** "Is this the right title?" (conservative, rule-based) is separate from
  "which release is best?" (scored).
- **Fail safe, not best guess.** Ambiguity ends in "no qualifying results", not a
  close-but-wrong download.
- **Hidden, never unrecoverable.** Nobody sees torrent names, but every job keeps the candidate
  it picked, the search that found it and its score, so a wrong download can be audited.
- **Secrets never reach the browser.** TMDB key, Plex tokens and the deploy key stay
  server-side.
- **The design spec is `design-exploration/obsidian.html`.** Tokens in
  `frontend/src/styles/tokens.css` mirror its System tab; change both together. The brand kit
  is in `brand_identity/obsidian-brand/`.

## Non-goals

- No multi-tenancy: one household, one Plex server, one Settings panel.
- No distributed job queue, CI/CD pipeline or Kubernetes.
- No Docker socket in the backend, ever.
