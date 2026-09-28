// Mirrors api.py's /api/recommendations.

import type { PosterCardItem } from '../components/media/PosterCard'
import type { BrowseParams, BrowseType } from '../api/browse'

/** One row the backend decided this person should see today. */
export interface RecommendedRow {
  /** Stable per row so React keeps a row's identity across refetches, and
   *  so the layout below can name it. */
  key: string
  title: string
  /** The muted second half of the heading — the "…watched *Dune*" part.
   *  Empty for the named rows, where the name is the whole point. */
  qualifier: string
  /** `"mixed"` means the row carries both, and each item says which it is
   *  — see `mediaTypeOf`. */
  media_type: 'movie' | 'tv' | 'mixed'
  items: (PosterCardItem & { media_type?: 'movie' | 'tv' })[]
  /** Where the row's "See all" goes: the browse page's own filters. Absent
   *  on the personal rows, which have no full list to go to. */
  browse?: (Partial<BrowseParams> & { type: BrowseType }) | null
}

export interface Recommendations {
  /** The UTC date these were dealt for. Present so a stale tab can tell. */
  day: string
  page: string
  rows: RecommendedRow[]
  /** Every row key on this page, in the order it should be rendered —
   *  including rows the frontend owns and the backend only names. */
  layout: string[]
}
