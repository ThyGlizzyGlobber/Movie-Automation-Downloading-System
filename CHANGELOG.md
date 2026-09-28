# Changelog

All notable changes to Obsidian are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

Every commit on `main` is a patch release of its own: the version is the latest
`vX.Y.0` tag with the patch number advanced once per commit since it, so the
commit after v1.0.0 runs as 1.0.1 without anyone bumping anything. This file
records the minor and major releases, and the patches worth calling out. See
[Versioning](README.md#versioning) for how a release is cut.

## [Unreleased]

### Fixed

- The home-screen app on iPhone drifted sideways while scrolling, on every page. A
  long row heading ran 2px past the screen edge in iOS WebKit, which made the page
  pannable; row headings now end in an ellipsis instead.
- "On Plex" and status badges could flash as blank pills while a row was scrolling
  on iPhone.

## [1.0.0] — 2026-09-28

The first versioned release: everything the household has been using, audited,
cleaned up and cut as a baseline.

### For the household

- **Browse like a streaming app.** Home, Movies and TV pages built on TMDB, with a
  hero carousel and inline trailers, Top 10, trending, popular, coming soon, rows by
  genre and by streaming service, and a Browse page that filters by all of them.
- **One-tap requests.** Pick a title and request it. Release choice, quality and
  sources are decided in the backend and never shown.
- **Knows what's already there.** Titles on Plex are badged everywhere they appear,
  and a content page plays them in Plex directly.
- **TV, properly.** Follow a show and new episodes arrive as they air, per episode,
  held back until a good copy exists. Request single episodes, whole seasons, a range
  of seasons or the complete series.
- **Continue watching** from Plex's On Deck, with playback progress.
- **Picked for you.** Rows drawn from what each person asks for, plus rotating mood
  rows in the household's language, dealt once a day.
- **Requests page** as a poster grid with live download progress.
- **Search** as a command palette (⌘K / Ctrl+K), with inline Request and In Plex
  actions and suggestions before you type.
- **Built for every screen.** A real layout per tier: tab bar on phones, top-bar
  navigation from 860px. Installable to the home screen as an app.

### For whoever runs it

- **Plex sign-in.** No separate passwords; access is checked against the linked Plex
  server, and its owner is admin.
- **Household panel** with per-person request permission and an audit log of
  sign-ins and changes.
- **A quality pipeline you control:** resolution floor, language rules, size bounds
  and a free-space floor, all from Settings.
- **Automatic filing.** Downloads are hardlinked into a Plex-shaped library, the
  household's audio track is marked as the default in the file and in Plex, and
  sources are cleaned up afterwards.
- **Dashboard and activity views** of what is searching, downloading, stuck or
  failed, and why.
- **Self-update** from Settings › Updates: a hardcoded `git pull` and a frontend
  rebuild, with no Docker socket.
- **Remote access** through a reverse proxy or Cloudflare Tunnel, with the setup and
  Plex-link routes held to the LAN.
- **Guided first run** through a setup wizard.

### Changed in this release

- Settings › About shows the release version (`v1.0.0`) with its build and date,
  instead of a bare commit hash.
- Pages other than Home, Movies, TV and the detail pages load on first visit, so the
  app starts on a bundle about 15% smaller.
- Request lists refresh from one shared poll (every 5 seconds while something is in
  flight, every 30 otherwise) instead of seven separate ones.
- The backend API is split into one module per area; the frontend is grouped into
  the app shell, shared UI and media components, and feature folders.

### Fixed

- Recommendations failed whenever Plex was linked and a row held a show.
- A rejected or stalled copy of an episode or season pack could be picked again.
- The Requests, Following, Search and person pages lost their poster grid until
  Browse had been opened; the account menu showed the Plex picture at full size.
- "Downloaded, not filed" showed green on Home.
- The re-download dialog ignored Escape.
- The command-line recovery tools ignored settings saved through the setup wizard.

[Unreleased]: https://github.com/ThyGlizzyGlobber/Movie-Automation-Downloading-System/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ThyGlizzyGlobber/Movie-Automation-Downloading-System/releases/tag/v1.0.0
