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

### Added

- **More than one Plex server.** An admin can link every Plex server their account
  owns from Settings › Plex, name them ("Immediate family", "Friends") and pick the
  primary. People sign in to the server they're shared on; someone shared on more
  than one lands on the primary.
- **Each server keeps its own requests.** Nobody sees what another server asked for.
  A title asked for on two servers still downloads once, and each server sees it
  under its own person's name. Following a show is per server too.
- **Switching servers.** An admin moves between servers from the account menu or
  Settings › Plex, and sees that server's requests, shows and library.
- After each download, every server refreshes and is put on the right audio track.
- **Release images.** Every push to `main` that passes the tests publishes backend and
  frontend images to GitHub Container Registry on the `beta` channel; a `vX.Y.Z` tag
  publishes `stable`. `truenas/obsidian-release.yaml` installs from them on TrueNAS 25.10,
  with no clone, deploy key or on-box build, and the version, commit and channel baked in.
- **Updates from the app.** Settings › Updates on a release install shows what's newer on
  the box's channel, with the release notes, and installs it with one button. The database
  is copied first. If the new version doesn't start within five minutes, the box goes back
  to the previous one by itself, restores that copy, and doesn't offer the failed release
  again. The installing is done by `truenas/obsidian-updater.py`, run every minute on the
  NAS by a TrueNAS cron job, so the app itself holds no TrueNAS credential.

- **The box's VPN, from the app.** On a box, qBittorrent can only reach the internet
  through the buyer's own VPN. The setup wizard asks for it (provider, then their WireGuard
  key or OpenVPN login, or any provider's WireGuard file), and Settings › VPN shows whether
  it's connected, through which country, and lets it be changed. The box's updater applies
  the details, so they end up only in TrueNAS's root-only app config.

- **Plex set up by signing in, on a box.** The box's own Plex server ships unclaimed. When
  the buyer signs in with Plex in the setup wizard, it's claimed for their account, linked,
  and given Movies and TV Shows libraries on the box's folders, with no Plex settings to
  visit.

- **A box's drives, each its own storage.** Downloads go to the drive with the most room and
  are filed on that same drive. Plex lists every drive's folders. Settings › Storage shows
  each drive with its space and health, and adds a new one, with any size in any free bay
  and a typed confirmation, since adding erases it. A drive that's missing is shown as
  such. A box with no drive yet keeps requests waiting until one is added.

### Changed

- With more than one server, replacing or deleting a copy (rejecting it, asking for
  an upgrade, cancelling a finished download) is admin-only, since every server
  plays from the same files.
- Settings › Household shows which server each person is on.
- On a release install, Settings › Updates shows the channel and version instead of the
  git-pull button, and `POST /api/admin/deploy` answers 409 there.

### Fixed

- Downloads didn't start if qBittorrent came up after Obsidian, as it does on a cold start
  with qBittorrent waiting on its VPN, until Obsidian was restarted. Obsidian now keeps
  trying and starts them as soon as qBittorrent answers.
- Browse pages failed with an error whenever the Plex server couldn't be reached;
  they now load without the "On Plex" badges until it's back.

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
