# Stage 0 — Feasibility Spike Notes

*Throwaway spike. No code from this stage carries forward — this note is the deliverable.*

**Run date:** 2026-09-04
**qBittorrent:** v5.2.3, Web API 2.15.1, at `192.168.0.133:30024` (no WebUI auth challenge from this network — allowlisted, not "no auth configured")
**TMDB:** v3 API key, tested live

---

## qBittorrent search plugins

**21 plugins enabled** out of the box: academictorrents, piratebay, torlock, sktorrent, zooqle, solidtorrents, bitsearch, uniondht, torrentdownloads, torrentscsv, kickasstorrents, torrentproject, goggames, acgrip, jackett, tsukihime, limetorrents, redetorrent, magnetdl, eztv, torrentgalaxy.

Ran 3 real searches (`dune part two 2024`, `mission impossible dead reckoning`, `john wick 4`) against `category=movies`, `plugins=enabled`, polled to completion (~45–55s), 1,497 total result rows.

### Only 6 of 21 plugins returned anything

| engine | rows | nbSeeders always valid? | fileSize always valid? | notes |
|---|---|---|---|---|
| torlock | 728 (48.6%) | **No — 100% report `-1`** | Yes | Site never exposes seed count via this plugin; huge share of total volume |
| limetorrents | 448 (29.9%) | Yes | Yes | Clean |
| piratebay | 290 (19.4%) | Yes | Yes | Clean |
| redetorrent | 24 (1.6%) | **No — 100% `-1`** | **No — 100% `-1`** | Both seeders and size unusable |
| torrentdownloads | 4 (0.3%) | Yes | Yes | Clean but negligible volume |
| jackett | 3 (0.2%) | N/A | N/A | **Misconfigured** — no API key set on this box, returns a single fake "result" row per query whose `fileName` is a Jackett config-error message and `fileUrl` points at Jackett's own local API (`http://127.0.0.1:9117`), not a torrent. Must be filtered out or the pipeline will try to add a garbage torrent. |

The other 15 enabled plugins (sktorrent, zooqle, solidtorrents, bitsearch, uniondht, torrentscsv, kickasstorrents, torrentproject, goggames, acgrip, tsukihime, eztv, magnetdl, torrentgalaxy, academictorrents) returned **zero rows** for all three queries — either no matching content, dead/blocked upstream, or timing out inside the search window. Not necessarily broken, but contributed nothing to this sample.

### Field quality, overall (n=1,497)

- `nbSeeders`: **never absent as a field**, but **50.4% are `-1`** (unknown) — almost entirely from torlock. Real numeric values only ~49% of the time.
- `fileSize`: never absent as a field, **98.2% valid** (only redetorrent + a handful of others report `0`/`-1`).
- `fileUrl` vs `descrLink`: **100% of rows had a non-empty `fileUrl`** — *but* this stat is misleading on its own. jackett's error-placeholder rows also populate `fileUrl` (with a bogus local address), so "fileUrl present" is not sufficient to prove "safe to add." No plugin in this sample returned `descrLink`-only rows for real torrents.

### Implications for Stage 2 design

- **`nbSeeders == -1` must be treated as "unknown," not "zero," and cannot be the primary key** — confirms the plan's stance that seeders is a tiebreaker/viability gate, not the main score. Given ~50% of real rows carry no seeder data (almost entirely from one very high-volume plugin, torlock), a seeders-first design would silently starve results from that plugin.
- **A plugin-level distrust list is needed**, not just per-field validation: `jackett` (misconfigured on this box, or structurally: any plugin whose row is an error message dressed as a result) must be excluded outright rather than filtered on missing fields. Likely worth a generic "does `fileUrl` look like a torrent/magnet source and not localhost/config host" sanity check, or specifically excluding `jackett` unless it's configured with a real indexer.
- **`redetorrent`'s rows are unusable for scoring** (no seeders, no size) — either drop it or let it flow through only as a last-resort fallback that skips the size/seeder-based tie-breaking.
- **fileSize is reliable enough (98%+) to gate/rank on directly**, matching the plan's assumption.
- Plugin diversity in results is very lopsided — 3 plugins (torlock, limetorrents, piratebay) account for 98% of volume. Fine for now (broad `plugins=enabled` search), but worth remembering if result counts ever look suspiciously low — it may mean one of those three specifically is down, not "no plugins working."

---

## TMDB API

Key tested live against `api.themoviedb.org/3`.

- **`search/movie`**: works cleanly. `Dune: Part Two` → single exact match, id `693134`, with `original_title`, `release_date`, `popularity`, `vote_average`/`vote_count` all populated.
- **`movie/{id}/alternative_titles`** (franchise title test, Dune: Part Two): **35 alternate titles** returned across regions. Of those, only a handful are Latin-script/English variants useful for query generation — `Dune 2`, `Dune: Part 2`, `Dune Two`, `Dune Part Two`. The remaining ~30 are transliterations/translations in Japanese, Vietnamese, Polish, Russian, Korean, Georgian, Chinese, Arabic-adjacent scripts, etc. — irrelevant to matching English-language torrent filenames and would need to be filtered by something like `iso_3166_1 in {US, GB, CA, AU}` or a Latin-script check before feeding Stage 1's variant generator, rather than used wholesale.
- **`watch/providers/movie`**: returns provider list with IDs (Netflix=8, Prime=9, Apple TV=350, Disney+=337, Hulu=15, etc.) — works.
- **`discover/movie?with_watch_providers=...&watch_region=US`**: works, returns properly filtered/sorted results (tested against Netflix, 4,748 total results, sorted by popularity).
- **`movie/popular`**: works, standard paginated response.

### Implications for Stage 1 design

- Alternate-titles data is **real but noisy** — the variant generator should not naively use every alternate title. Confirms the plan's existing design (canonical title, `original_title` if different, title-without-subtitle, title+year — a small curated set) over "throw every AKA at the search plugins." A cheap Latin-script/region filter on alternative_titles would be a reasonable *additional* variant source later, but isn't needed for the MVP variant list.
- No surprises on the discover/popular/provider endpoints — Stage 1 can build directly against the assumed contract.

---

## Overall verdict

Plan's assumptions **mostly survive contact with real data**, with two concrete adjustments to carry into Stage 2:

1. Add explicit plugin distrust/exclusion handling (at minimum: drop rows from misconfigured `jackett`; treat `redetorrent`-style all-`-1` plugins as size/seeder-blind).
2. Don't treat "has a `fileUrl`" as sufficient signal of a real result — a plugin can populate `fileUrl` with a non-torrent URL when it's erroring out.

No changes needed to Stage 1's TMDB assumptions.
