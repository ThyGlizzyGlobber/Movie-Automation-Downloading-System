# Smithflix — Brand Implementation Guide

This folder contains the Smithflix brand kit for the Smith family's
streaming app (`frontend/index.html`, a single self-contained file). This
doc describes what's **actually live on the site today** — kept in sync
each time the brand is touched, so a future session/agent reading this
doesn't rebuild something already done differently, or reintroduce
something that was deliberately removed. If you change how any of this
works in the site, update this file in the same pass.

## 1. Colors

| Token                  | Hex       | Usage                                                   |
|-------------------------|-----------|------------------------------------------------------------|
| `--smithflix-cream`     | `#f6efe3` | Light-mode background base, cream text on dark surfaces  |
| `--smithflix-teal`      | `#123b36` | Primary mark color, "smith" wordmark (light mode), dark-mode base |
| `--smithflix-coral`     | `#ff6b4a` | Fixed accent — "flix" wordmark, buttons/links/active states, play/action cues |
| `--smithflix-mustard`   | `#f4b942` | Secondary accent, defined but not currently used anywhere in the UI |

Coral is the app's **one fixed accent color across both themes** — it is
not user-customizable. An earlier per-household accent-color picker
(`/api/settings/appearance`, a Settings-panel color swatch feature) was
**removed entirely** — frontend UI, JS, backend routes, and tests — so the
brand color can't be overridden back to something off-brand. Do not
rebuild that feature; if a future request wants per-household
customization back, that's a deliberate new decision to make with the
user, not something to silently restore.

CSS variables, exactly as they exist in `frontend/index.html`'s `:root`:

```css
:root {
  --smithflix-cream: #f6efe3;
  --smithflix-teal: #123b36;
  --smithflix-coral: #ff6b4a;
  --smithflix-mustard: #f4b942;
}
```

### Derived theme tokens

The app has a real dark/light toggle (see §4). Both themes are built from
the four brand colors above, but the site's own semantic tokens
(`--bg`/`--text`/etc.) are **not** identical to the raw brand hexes —
they're tuned surface/text variants for actual UI contrast. These are the
real, current values:

```css
/* Dark (default) */
:root {
  --bg: #0d2b27;
  --bg-elevated: #123b36;   /* == --smithflix-teal exactly */
  --bg-card: #17453f;
  --text: #f6efe3;          /* == --smithflix-cream */
  --text-dim: #a9c1bc;
  --accent: var(--smithflix-coral);
  --accent-text: #ffffff;
  --border: #285650;
}
/* Light — :root[data-theme="light"] overrides just these */
--bg: #f6efe3;               /* == --smithflix-cream */
--bg-elevated: #efe5d2;
--bg-card: #e8dcc4;
--text: #123b36;             /* == --smithflix-teal */
--text-dim: #5c7570;
--border: #ddceac;
```

`--accent`/`--accent-text` are **not** redeclared for light mode — coral
reads fine on both backgrounds, so it's set once in the base `:root` and
inherited by both themes. Semantic status colors (queued/searching/
downloading/complete/no-match/no-space/failed) are a separate, older
convention and are deliberately **not** brand-recolored or theme-varied —
don't fold them into this palette.

**Known trap, already fixed once — don't reintroduce it:** never hardcode
a literal dark color (e.g. `rgba(15,17,21,0.9)`) for a background/gradient
that should track the current theme. Use `var(--bg)` directly, or
`color-mix(in srgb, var(--bg) N%, transparent)` for a translucent version.
Three spots in the CSS (topbar, tabbar, hero gradient) originally hardcoded
dark-only literals and broke silently in light mode until caught and fixed.

## 2. Typography

- **Baloo 2** (weights 600 / 700 / 800) — the wordmark and headings.
  Rounded, bold, friendly.
- **Inter** (weights 400 / 500 / 600) — body text and UI copy, set as the
  page's base `font-family` via `--font-body`.

Google Fonts import, exactly as it exists in `frontend/index.html`'s
`<head>` (loaded alongside the icon font, see §3):

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
```

```css
:root {
  --font-display: 'Baloo 2', sans-serif;
  --font-body: 'Inter', -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
}
html, body { font-family: var(--font-body); }
```

`--font-display` is used for the logo wordmark and page/section headings
only; everything else stays on `--font-body`.

### The logo wordmark is real text, not an image

There is **no combined icon+wordmark logo file** in this kit (no
`logo-horizontal-*.svg`, no badge `icon.svg` with the wordmark baked in —
if you're looking for one, it doesn't exist; don't recreate it as a
static image). The header logo (top-left, no icon mark next to it — that
was deliberately removed) is two colored `<span>`s, sized larger than
body text, set directly in the page's own CSS:

```html
<a id="topbarLogo" href="#/home" aria-label="Smithflix home">
  <span class="brand-wordmark"><span class="brand-word-smith">smith</span><span class="brand-word-flix">flix</span></span>
</a>
```

```css
#topbarLogo .brand-wordmark {
  font-family: var(--font-display); font-weight: 800; font-size: 26px; line-height: 1;
}
#topbarLogo .brand-word-smith { color: var(--text); }   /* tracks the active theme */
#topbarLogo .brand-word-flix { color: var(--smithflix-coral); }  /* always coral */
```

This is deliberate: "smith" recolors automatically with the theme toggle
(inherits `--text`), "flix" always stays coral. No JS is needed to swap
images on theme change — plain CSS variable inheritance handles it. The
`wordmark-dark.svg`/`wordmark-light.svg` files that used to back an
`<img>`-based version of this were removed from `frontend/` once the site
moved to text (they may still exist in this kit's `logo/` folder as
reference/history, but nothing in the live site loads them anymore).

## 3. Icons — Material Symbols Rounded (not a hand-drawn SVG set)

Every icon in the app (settings gear, Home/Movies/TV tab icons, the
downloads FAB, and anything added since) is Google's **Material Symbols
Rounded** variable font, rendered as icon-ligature `<span>`s — not
individually hand-embedded SVG `<path>` data. Fixed parameters across the
whole site, baked into the font URL itself:

- Fill: **1** (filled, not outlined)
- Weight: **700**
- Grade: **0**
- Optical size: **48**

```html
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@48,700,1,0" rel="stylesheet">
```

```css
.material-symbols-rounded {
  font-family: 'Material Symbols Rounded';
  font-weight: normal; font-style: normal; line-height: 1;
  letter-spacing: normal; text-transform: none; display: inline-block;
  white-space: nowrap; word-wrap: normal; direction: ltr;
  font-variation-settings: 'FILL' 1, 'wght' 700, 'GRAD' 0, 'opsz' 48;
}
```

Usage — a plain `<span>` with the icon's real Material Symbols name as
its text content, colored via ordinary `color`/`currentColor` inheritance
from its parent button/element:

```html
<button aria-label="Settings"><span class="material-symbols-rounded">settings</span></button>
```

Per-context size is set via `font-size` on the wrapping context, not on
the icon itself (the `opsz` axis is fixed at 48 regardless of rendered
size): 28px on the downloads FAB, 22px on bottom-tab icons, 20px on the
settings/theme-toggle buttons (inherited from those buttons' own
`font-size: 20px`).

**When adding a new icon**, find its real name at
[Google Fonts' Material Symbols picker](https://fonts.google.com/icons)
and use that exact string as the span's text content — don't hand-draw an
SVG path for it. This keeps every icon in the app visually identical
(same weight/grade/fill/opsz) with zero extra effort.

## 4. Dark / light mode

A real, user-toggleable theme — not just `prefers-color-scheme`. The
toggle button sits immediately to the right of the Settings gear in the
header.

- State lives in `localStorage` under the key `smithflix-theme`
  (`"light"` or `"dark"`; anything else, including unset, means dark).
- `document.documentElement`'s `data-theme` attribute drives everything —
  `:root[data-theme="light"]` in CSS overrides the surface/text tokens
  (see §1).
- To avoid a flash of the wrong theme before the stylesheet/JS finish
  loading, a **tiny inline `<script>` in `<head>`** (before the main
  `<style>` block) sets `data-theme` synchronously from `localStorage`,
  before first paint:
  ```html
  <script>try{document.documentElement.dataset.theme=localStorage.getItem("smithflix-theme")==="light"?"light":"dark"}catch(e){}</script>
  ```
- The main script (at the bottom of the page, once the rest of the DOM
  exists) handles everything the head-script can't: the toggle button's
  own icon (`light_mode`/`dark_mode`, swapped) and label, and the
  `<meta name="theme-color">` tag (`#123b36` dark / `#f6efe3` light).
- The wordmark needs **no** JS handling on theme change — see §2, it's
  plain CSS variable inheritance.

Default is dark if nothing is saved yet.

## 5. File inventory (what's actually in this folder right now)

```
smithflix-brand/
├── BRAND-IMPLEMENTATION.md      (this file)
├── manifest.json                 kept in sync with frontend/manifest.json
├── icons/
│   ├── favicon.svg                current icon mark, teal, transparent bg
│   ├── favicon.png                80×80 PNG of the same mark
│   ├── icon-512.png                512×512 PNG, byte-identical to icon-mark-only.png
│   └── icon-mark-only.{svg,png}   same mark again, no badge — kept for the
│                                   "no badge, any surface" watermark use case
└── logo/
    ├── wordmark-dark.svg           historical reference only — the live site
    │                               no longer loads this (see §2)
    └── wordmark-light.svg          same
```

There is currently **no** `logo/icon.svg` (badge version), no
`logo-horizontal-*.svg`, and no `apple-touch-icon.png` in this kit as a
separate file — if you see references to those elsewhere (an older
version of this doc, a stale zip), they describe a **previous, replaced**
icon design (a teal house with a coral play triangle inside a cream
badge) that is no longer current. The current icon (confirmed directly
with the user) is the plain teal house/home shape in `icons/favicon.svg` —
no play triangle, no badge.

## 6. Where each file goes in `frontend/`

The live site keeps its own flat copies of the files it actually needs,
committed directly in `frontend/` (not referencing this folder at
runtime):

- `frontend/brand-icon.svg` ← `icons/favicon.svg` (or `icon-mark-only.svg`
  — identical). Used for **both** the favicon and used to be used for the
  header icon (now removed from the header — see §2 — but the file still
  backs the favicon `<link>`).
- `frontend/icon-512.png` ← `icons/icon-512.png`. PWA manifest large icon.
- `frontend/apple-touch-icon.png` ← same PNG content as `icon-512.png`
  (no dedicated full-bleed 180×180 apple-touch asset exists in this kit;
  the browser downsizes the 512px one). If a real dedicated apple-touch
  asset is ever produced, swap it in here.
- No `icon-192.png` — deleted outright rather than kept mismatched, since
  no real 192px asset exists in this kit. The manifest declares the SVG
  (`sizes: "any"`) plus the 512px PNG instead; don't re-add a 192 entry
  without a real 192px file to back it.

**Favicon `<head>` links, exactly as live:**
```html
<link rel="icon" type="image/svg+xml" href="/brand-icon.svg">
<link rel="icon" type="image/png" sizes="512x512" href="/icon-512.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#123b36">
```

**Header logo:** plain text, not an image file at all — see §2. Don't
place `icon.svg`/`icon-mark-only.svg` next to it in the header; that icon
mark was deliberately removed from the header on request, and now only
backs the favicon.

## 7. Notes for implementation

- The site is fully self-contained (`frontend/index.html`, no build
  step) — any brand change happens directly in that file's `<head>`/
  `<style>`/markup, not by adding new asset-loading indirection.
- Google Fonts (`fonts.googleapis.com`/`fonts.gstatic.com`) is an
  accepted external dependency — the app already reaches the internet
  for TMDB poster images, so this isn't a new class of dependency.
- Keep this document and `manifest.json` in this folder in sync with
  `frontend/index.html`/`frontend/manifest.json` whenever either changes
  — this file exists specifically so a fresh session doesn't have to
  reverse-engineer the live site to know what's current.
