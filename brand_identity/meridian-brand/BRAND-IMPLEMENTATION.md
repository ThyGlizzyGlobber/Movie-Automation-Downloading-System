# Meridian — Brand Implementation Guide

This folder is the brand kit for Meridian, the Smith household's Plex
request app (`frontend/`, a React + Vite app; formerly "Smithflix", and
before that "The Family Downloader"). This doc describes what is
**actually live in the app today** and is kept in sync each time the
brand is touched, so a future session doesn't rebuild something already
done differently or reintroduce something deliberately removed. If you
change how any of this works in the app, update this file in the same
pass.

The visual reference for everything below is `design-exploration/
obsidian.html` (the chosen "Obsidian" direction, expanded to eleven
screens) and, for the record of how it was chosen, `design-exploration/
redesign-directions.html` (Obsidian against two rejected directions,
Marquee and Folio). The reference files are standalone HTML and are not
built into the app.

## 1. Name

**Meridian.** Renamed from Smithflix on 2026-09-15 at the owner's request,
in the same pass that adopted the Obsidian design direction. The name
appears as a single word in the display face, never split or two-toned
the way the old "smith"/"flix" wordmark was. Where it appears:

- `frontend/index.html` `<title>` and `apple-mobile-web-app-title`
- `frontend/public/manifest.json` `name` / `short_name`
- `frontend/src/lib/chrome.tsx` — `document.title` (`"<page> — Meridian"`)
- `Topbar.tsx`, `MovieDetailPage.tsx`, `ShowDetailPage.tsx` — the wordmark
- `LoginPage.tsx`, `TutorialOverlay.tsx`, `UpdatesPanel.tsx` — plain text
- `backend/app/api.py` FastAPI title and `backend/app/plex.py`
  `PRODUCT_NAME` (the label Plex shows for this app on sign-in; the client
  identifier is unchanged, so existing sign-ins are unaffected)

The frontend package is `meridian-frontend`. Storage paths, env vars,
cookies, the TrueNAS app name (`movie-downloader`) and container names
never carried the old name and were not touched.

## 2. Mark

A rounded square (radius 28% of its side) filled with a diagonal
gradient from `#7f8bff` (periwinkle) to `#3fd6c8` (teal), carrying a
geometric "M" drawn as a single round-capped stroke in the page black
`#050508`. It is the same shape language as the app's glass pills and
cards. Source of truth: `icons/icon-mark-only.svg` (identical to
`frontend/public/brand-icon.svg`). PNGs in `icons/` are rasterised from
it; regenerate them from the SVG rather than editing them.

In the app the mark sits to the left of the wordmark in the top bar
(32px) and the detail-page footer (28px), and alone as the PWA icon,
favicon and login-card badge.

## 3. Color

Dark is the only theme. There is no light mode and no toggle (removed
deliberately in an earlier pass; do not restore without a decision).

The ground is `#050508`, a blue-black rather than pure black: the hint of
blue is what lets the ambient glows and glass surfaces read as depth.

| Token                    | Value                      | Role |
|--------------------------|----------------------------|------|
| `--meridian-black`       | `#050508`                  | page ground; hero and detail fades blend into it |
| `--meridian-ink`         | `#f5f7fa`                  | text |
| `--meridian-ink-muted`   | `rgba(245,247,250,.55)`    | secondary text, labels |
| `--meridian-ice`         | `#9cc0ff`                  | interactive accent; downloading |
| `--meridian-mint`        | `#6fe3c1`                  | complete / in Plex |
| `--meridian-violet`      | `#c39cff`                  | searching |
| `--meridian-coral`       | `#ff8a80`                  | failed |
| `--meridian-amber`       | `#ffcf7a`                  | no match; storage |
| `--meridian-orange`      | `#ffb086`                  | no space |
| `--meridian-star`        | `#ffd166`                  | rating star only |

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
- **Not yet:** component restyle to the reference (glass nav pill, white
  primary buttons, ring progress, Top 10 row, search palette, request
  modal, settings layout), icon set migration, poster-derived glows.

## 7. Files

```
meridian-brand/
├── BRAND-IMPLEMENTATION.md   this file
├── manifest.json             copy of frontend/public/manifest.json
└── icons/
    ├── icon-mark-only.svg    source of truth for the mark
    ├── icon-512.svg, favicon.svg
    └── icon-512.png, icon-72.png, favicon.png, icon-mark-only.png
```
