import type { RequestOut } from '../types/requests'

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

// A pack row's scope, as a short label — "Season N", "Season N-M" (a
// range pack), or "Complete Series".
export function packScopeLabel(r: RequestOut): string {
  if (r.season_number == null) return 'Complete Series'
  if (r.season_range_end != null) return `Season ${r.season_number}-${r.season_range_end}`
  return `Season ${r.season_number}`
}

// The Watching list's second pill — what a show's most recent episode/
// pack request actually covers, e.g. "S03E12" or "Season 4".
export function latestRequestLabel(r: RequestOut): string {
  if (r.media_type === 'episode') return `S${pad2(r.season_number!)}E${pad2(r.episode_number!)}`
  return packScopeLabel(r)
}

// A standalone row's full label/href — movies (always standalone) and
// any episode/pack row that somehow has no show_id to group under.
export function requestLabelAndHref(r: RequestOut): { label: string; href: string } {
  if (r.media_type === 'episode') {
    return { label: `${r.title} S${pad2(r.season_number!)}E${pad2(r.episode_number!)}`, href: `#/tv/${r.tmdb_id}` }
  }
  if (r.media_type === 'pack') {
    return { label: `${r.title} — ${packScopeLabel(r)}`, href: `#/tv/${r.tmdb_id}` }
  }
  return { label: `${r.title}${r.release_year ? ` (${r.release_year})` : ''}`, href: `#/movies/${r.tmdb_id}` }
}

// A leaf row's label under a season group — omits the show title (the
// show-group header above it) and the season (the season-group header
// above *that*), leaving just the episode, or the pack's own scope for a
// season that only has a pack row so far.
export function episodeLabelAndHref(r: RequestOut): { label: string; href: string } {
  if (r.media_type === 'episode') {
    return { label: `E${pad2(r.episode_number!)}`, href: `#/tv/${r.tmdb_id}` }
  }
  return { label: packScopeLabel(r), href: `#/tv/${r.tmdb_id}` }
}

// Which single status best represents a whole group at a glance —
// anything still actively in motion (downloading/searching/queued)
// outranks a terminal one, so "one episode is still downloading" never
// gets hidden behind five other episodes that already finished.
const GROUP_STATUS_PRIORITY = [
  'downloading',
  'searching',
  'queued',
  'no qualifying results',
  'insufficient free space',
  'downloaded, not filed',
  'failed',
  'complete',
  'cancelled',
]

export function dominantStatus(rows: RequestOut[]): string {
  for (const s of GROUP_STATUS_PRIORITY) {
    if (rows.some((r) => r.status === s)) return s
  }
  return rows[0].status
}

export interface StandaloneItem {
  type: 'standalone'
  repId: number
  row: RequestOut
}

// One season (or the season-less "Complete Series" bucket) within a
// show's group — a season that has both its own bulk pack row and
// individual episode rows shows the pack as one of its own rows
// alongside them, not specially merged; `key` disambiguates "Complete
// Series" (season_number null) from a real season number.
export interface SeasonGroup {
  key: string
  label: string
  rows: RequestOut[]
  repId: number
}

export interface ShowGroup {
  type: 'show'
  repId: number
  showId: number
  tmdbId: number
  title: string
  posterPath: string | null
  rows: RequestOut[]
  seasons: SeasonGroup[]
}

export type DisplayItem = StandaloneItem | ShowGroup

// Groups episode/pack rows by show, then by season within that show
// (Part J3's drill-down) — "Lanterns" appears once, expandable into its
// own seasons, each independently expandable into its episodes. Movies
// stay standalone. Sorted by each item's/season's most recent row id, so
// the merged list (and each show's own season list) still reads newest-
// activity-first, same as the flat order did before grouping existed.
export function groupRequestsForDisplay(rows: RequestOut[]): DisplayItem[] {
  const showsById = new Map<number, ShowGroup>()
  const items: DisplayItem[] = []

  for (const r of rows) {
    if (r.media_type === 'movie' || r.show_id == null) {
      items.push({ type: 'standalone', repId: r.id, row: r })
      continue
    }

    let show = showsById.get(r.show_id)
    if (!show) {
      show = {
        type: 'show',
        showId: r.show_id,
        tmdbId: r.tmdb_id,
        title: r.title,
        posterPath: r.poster_path,
        rows: [],
        seasons: [],
        repId: r.id,
      }
      showsById.set(r.show_id, show)
      items.push(show)
    }
    show.rows.push(r)
    show.repId = Math.max(show.repId, r.id)
    // Any row's poster will do when the first one has none.
    show.posterPath ??= r.poster_path

    const key = r.season_number == null ? 'series' : String(r.season_number)
    let season = show.seasons.find((s) => s.key === key)
    if (!season) {
      season = { key, label: packScopeLabel(r), rows: [], repId: r.id }
      show.seasons.push(season)
    }
    season.rows.push(r)
    season.repId = Math.max(season.repId, r.id)
  }

  for (const item of items) {
    if (item.type === 'show') item.seasons.sort((a, b) => b.repId - a.repId)
  }
  return items.sort((a, b) => b.repId - a.repId)
}
