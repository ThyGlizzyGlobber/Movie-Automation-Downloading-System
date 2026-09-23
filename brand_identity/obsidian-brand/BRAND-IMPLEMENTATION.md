# Obsidian — Brand Implementation Guide

> **Obsidian: Automated Media Downloading Made Simple**

This folder is the brand kit for Obsidian, the Smith household's Plex
request app (`frontend/`, a React + Vite app; formerly "Meridian",
before that "Smithflix", and before that "The Family Downloader"). This
doc describes what is **actually live in the app today** and is kept in
sync each time the brand is touched, so a future session doesn't rebuild
something already done differently or reintroduce something deliberately
removed. If you change how any of this works in the app, update this
file in the same pass.

**One name, two meanings — worth knowing before you read on.** "Obsidian"
was the name of the *visual direction* months before it was the name of
the product: it beat Marquee and Folio in the 2026-09-15 exploration, and
the app was still called Meridian at the time. The 2026-09-20 rename
simply named the product after its own design language, so the two now
coincide. Where this guide says "the Obsidian direction" or "the Obsidian
reference" it means the design exploration; where it says "Obsidian" on
its own it means the app.

The visual reference for everything below is `design-exploration/
obsidian.html` (that direction, expanded to eleven screens) and, for the
record of how it was chosen, `design-exploration/redesign-directions.html`
(Obsidian against the two rejected directions, Marquee and Folio). The
reference files are standalone HTML, keep their original filenames, and
are not built into the app.

## 1. Name

**Obsidian.** The tagline is **"Obsidian: Automated Media Downloading
Made Simple"** — used as a strapline for the README and anywhere the app
introduces itself, not as part of the wordmark, which stays the bare
word.

Lineage: The Family Downloader → Smithflix → **Meridian** (2026-09-15,
the same pass that adopted the Obsidian design direction) → **Obsidian**
(2026-09-20), each at the owner's request. The name appears as a single
word in the display face, never split or two-toned the way the old
"smith"/"flix" wordmark was. Where it appears:

- `frontend/index.html` `<title>` and `apple-mobile-web-app-title`
- `frontend/public/manifest.json` `name` / `short_name`
- `frontend/src/lib/chrome.tsx` — `document.title` (`"<page> — Obsidian"`)
- `Topbar.tsx`, `MovieDetailPage.tsx`, `ShowDetailPage.tsx` — the wordmark
- `LoginPage.tsx`, `TutorialOverlay.tsx`, `UpdatesPanel.tsx` — plain text
- `backend/app/api.py` FastAPI title and `backend/app/plex.py`
  `PRODUCT_NAME` (the label Plex shows for this app on sign-in; the client
  identifier is unchanged, so existing sign-ins are unaffected)

The frontend package is `obsidian-frontend`. Storage paths, env vars,
cookies, the TrueNAS app name (`movie-downloader`) and container names
have never carried any product name and were not touched by either
rename.

The design tokens renamed with the app: the primitive palette is
`--obsidian-*` (was `--meridian-*`), 51 references across 10 files. The
semantic aliases components actually consume (`--bg`, `--text`,
`--accent`, `--status-*`) were already name-neutral and did not move.

## 2. Mark

**The obsidian shard** (2026-09-23), replacing the "M" drawn for
Meridian. A faceted gem split into three slices, drawn flat in four
shades: `#9cc0ff` (`--obsidian-ice`) on the lit faces, `#3644d6`
(`--glow-1`) on the body, `#2d39b2` on the mid-shadow faces and
`#1c2270` in the undersides and the gaps between slices.

As an app icon the shard sits centred at 0.53 scale on a vertical
gradient that holds `#050508` for the top ~11% and ramps to violet
`#8445ff` at the bottom, so the ice faces sit on near-black and the
violet reads as light coming from under the gem.

Sources, all in `icons/`:

- `Obsidian-Logo-Icon-Only.svg`: **the source of truth.** The shard
  alone on a transparent background.
- `Obsidian-Logo-Icon.svg`: the shard on a flat `#050508` square.
- `Obsidian-Logo-Squared.af` / `.png`: the Affinity working file and its
  export.
- `iOS App Icon.icon`: the Icon Composer bundle, for a future native
  app. The website doesn't use it.
- `icon-tile.svg` / `icon-1024.png`: the app-icon composition (gradient
  plus shard).
- `build_icons.py`: renders the composition from the shard SVG.

Don't edit the rasters. Change the shard SVG, then run
`python3 brand_identity/obsidian-brand/icons/build_icons.py` from the
repo root. That writes every icon in `frontend/public/`:

- `brand-icon.svg`: the rounded tile. Used for the favicon, the manifest
  `any` icon and the login-card badge.
- `apple-touch-icon.png` (180): square and opaque, because iOS applies
  its own mask and fills any transparency with black.
- `icon-192.png`, `icon-512.png`: the manifest icons.
- `favicon-32.png`: the PNG favicon fallback.

The top bar carries the wordmark alone, with no mark beside it.

## 3. Color

Dark is the only theme. There is no light mode and no toggle (removed
deliberately in an earlier pass; do not restore without a decision).

The ground is `#050508`, a blue-black rather than pure black: the hint of
blue is what lets the ambient glows and glass surfaces read as depth.

| Token                    | Value                      | Role |
|--------------------------|----------------------------|------|
| `--obsidian-black`       | `#050508`                  | page ground; hero and detail fades blend into it |
| `--obsidian-ink`         | `#f5f7fa`                  | text |
| `--obsidian-ink-muted`   | `rgba(245,247,250,.55)`    | secondary text, labels |
| `--obsidian-ice`         | `#9cc0ff`                  | interactive accent; downloading |
| `--obsidian-mint`        | `#6fe3c1`                  | complete / in Plex |
| `--obsidian-violet`      | `#c39cff`                  | searching |
| `--obsidian-coral`       | `#ff8a80`                  | failed |
| `--obsidian-amber`       | `#ffcf7a`                  | no match; storage |
| `--obsidian-orange`      | `#ffb086`                  | no space |
| `--obsidian-star`        | `#ffd166`                  | rating star only |

Only ice is decorative. Every other color is a status and always means
that status, so a colored chip is never just ornament.

**Glass.** A surface is white at low alpha over the ground with a 1px
lighter border and backdrop blur: `--glass` (6% white), `--glass-strong`
(10%), `--glass-border` (10%), `--glass-blur` (`blur(30px) saturate(150%)`).
Floating overlays (search palette, modals, the nav pill) use
`--glass-overlay`, `rgba(16,16,24,.72)`, so they stay legible over bright
artwork.

**Ambient glow.** Three blurred blobs behind hero and detail art. House
defaults are `--glow-1 #3644d6`, `--glow-2 #1aa79c`, `--glow-3 #6f3ad6`. A
page with a featured title derives its own from the poster's colors (main
glow on the poster hue, second ~48° warmer, third ~60° cooler); pages
without one keep the defaults.

**Semantic aliases.** Components consume `--bg`, `--bg-card`,
`--bg-elevated`, `--text`, `--text-dim`, `--on-light`, `--accent`,
`--accent-text`, `--accent-secondary`, `--border` and the seven
`--status-*` tokens. Their names predate the repaint; their values are
defined in `frontend/src/styles/tokens.css`, which is the source of truth
for every number above.

## 4. Type

| Role | Face | Weight | Notes |
|------|------|--------|-------|
| Display: hero, page titles | Outfit | 300 | `--text-hero` / `--text-page`, tracking -0.045em, leading 1 |
| Top 10 numerals | Outfit | 200 | gradient-clipped "glass" fill |
| Section heads | Outfit | 400 + 300 | qualifier word in 300 muted ("Continue *watching*") |
| Card / row titles | Outfit | 400 | 15–19px, tracking -0.02em |
| Wordmark | Outfit | 500 | 24px in the top bar |
| Body | Manrope | 500 | 17px / 1.65 |
| Labels, nav, pills, buttons | Manrope | 700 | 12–14px |
| Eyebrows / captions | Manrope | 700–800 | 11–12px, uppercase, +0.08em |
| Paths and hosts (Settings) | IBM Plex Mono | 400–500 | 13px |

Fonts load from Google Fonts via `frontend/index.html`; `frontend/
nginx.conf`'s Content Security Policy already allows `fonts.googleapis.com`
and `fonts.gstatic.com`. Outfit is loaded 200–700, Manrope 400–800, Plex
Mono 400 and 500. Tokens: `--font-display`, `--font-body`, `--font-mono`.

Icons are still Material Symbols Rounded (filled, weight 700) as before;
moving them to the 1.75px round-capped stroke set drawn in the reference
is a later component pass.

## 5. Shape and motion

Radii: 10 / 16 / 22 / 30px and pill (`--radius-sm` … `--radius-xl`,
`--radius-pill`). Posters 16–18px, glass cards 22–30px, every button and
chip a pill. Cards lift 6px on hover. Progress is a glowing ring, not a
bar, except the thin "continue watching" line under landscape cards.

## 6. Adoption status

- **Done (2026-09-15):** tokens, fonts, name, mark, wordmark, PWA
  manifest, theme color, this guide.
- **Done (2026-09-20):** rename to Obsidian — every user-facing and
  code-facing string, the `--obsidian-*` token prefix, the npm package,
  this kit's folder name, and this guide.
- **Done (2026-09-23):** the obsidian shard replaced the "M" mark
  across the favicon, the PWA and iOS home-screen icons, and the login
  badge (see §2).
- **Not yet:** component restyle to the reference (glass nav pill, white
  primary buttons, ring progress, Top 10 row, search palette, request
  modal, settings layout), icon set migration, poster-derived glows.

## 7. Files

```
obsidian-brand/
├── BRAND-IMPLEMENTATION.md   this file
├── manifest.json             copy of frontend/public/manifest.json
└── icons/
    ├── Obsidian-Logo-Icon-Only.svg   source of truth: the shard
    ├── Obsidian-Logo-Icon.svg        the shard on #050508
    ├── Obsidian-Logo-Squared.af/.png Affinity working file and export
    ├── iOS App Icon.icon/            Icon Composer bundle (native, unused by the web app)
    ├── apple-touch-icon.png          designer's 1024 export (rounded, for reference)
    ├── icon-tile.svg, icon-1024.png  the app-icon composition
    └── build_icons.py                renders every icon in frontend/public/
```
