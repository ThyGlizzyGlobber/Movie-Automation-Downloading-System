import type { RequestOut } from '../types/requests'
import { FAILED_STATES, NON_TERMINAL } from './status'

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

// A pack row's scope, as a short label — "Season N", "Season N-M" (a
// range pack), or "Complete series".
export function packScopeLabel(r: RequestOut): string {
  if (r.season_number == null) return 'Complete series'
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
  // Last, below even cancelled: it carries no news of its own. A show
  // whose series request split into seasons is exactly as done as those
  // seasons are, and its card should say so.
  'split',
]

export function dominantStatus(rows: RequestOut[]): string {
  for (const s of GROUP_STATUS_PRIORITY) {
    if (rows.some((r) => r.status === s)) return s
  }
  return rows[0].status
}

// What a row asks for: one episode, one season's pack, a range, the
// whole series. Two rows with the same scope are two attempts at the
// same thing.
function scopeKey(r: RequestOut): string {
  return [r.media_type, r.season_number ?? '', r.season_range_end ?? '', r.episode_number ?? ''].join('|')
}

const GAVE_UP = new Set([...FAILED_STATES, 'cancelled'])

// A show's rows without the attempts a later one has replaced. Asking
// again after a failure leaves the failure behind, and it used to go on
// speaking for the show: Wonka's The Golden Ticket had every episode on
// Plex and still read "No match", 9 of 20, because the first search's
// nine misses sat beside the nine that worked (2026-10-05).
//
// A failed or cancelled attempt goes once anything newer asks for the
// same thing. Anything goes once something newer for it has worked or
// is under way. What's left alone is the one case where the older row
// is still the truth: a file on Plex whose replacement attempt failed —
// the file is still there.
export function withoutSuperseded(rows: RequestOut[]): RequestOut[] {
  const byScope = new Map<string, RequestOut[]>()
  for (const r of rows) {
    const key = scopeKey(r)
    byScope.set(key, [...(byScope.get(key) ?? []), r])
  }
  return rows.filter((r) =>
    !(byScope.get(scopeKey(r)) ?? []).some((s) => s.id > r.id && (GAVE_UP.has(r.status) || !GAVE_UP.has(s.status))),
  )
}

// "9 of 9 on Plex": the rows that deliver something. A row that split
// into seasons or episodes delivers nothing itself — its parts are
// counted instead — so it would only ever sit in the total, unfillable.
export function onPlexTally(rows: RequestOut[]): { ready: number; total: number } {
  const deliverable = rows.filter((r) => r.status !== 'split')
  return { ready: deliverable.filter((r) => r.status === 'complete').length, total: deliverable.length }
}

export interface StandaloneItem {
  type: 'standalone'
  repId: number
  row: RequestOut
}

// One season (or the season-less "Complete series" bucket) within a
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

  // Movies too: a film asked for again after a miss is one card.
  for (const r of withoutSuperseded(rows)) {
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

// A show's own progress: the average over everything it has on the way
// or done — a finished season counts as 100%, a downloading one as its
// own percentage, one still waiting or searching as 0%. Failed and
// cancelled rows don't count. Null when nothing is moving or done.
export function groupProgress(rows: RequestOut[]): number | null {
  const counted = rows.filter((r) => NON_TERMINAL.has(r.status) || r.status === 'complete' || r.status === 'downloaded, not filed')
  if (!counted.length) return null
  const total = counted.reduce((sum, r) => {
    if (r.status === 'complete' || r.status === 'downloaded, not filed') return sum + 1
    if (r.status === 'downloading') return sum + (r.download_progress ?? 0)
    return sum
  }, 0)
  return total / counted.length
}
