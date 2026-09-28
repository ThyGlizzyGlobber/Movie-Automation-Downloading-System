import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getRecommendations } from '../../api/recommendations'
import { browseHref } from '../../api/browse'
import MediaRow from '../../components/media/MediaRow'
import type { RecommendedRow } from '../../types/recommendations'

/** One row the backend filled: a personal one ("Because you watched…",
 *  "Today's top picks"), a named one ("Comedies That Go Somewhere Dark"),
 *  or a catalogue one (Trending, a genre, a service). */
function RecommendedMediaRow({ row }: { row: RecommendedRow }) {
  // A "mixed" row carries films and shows together; each item says which
  // it is. Falling back to movie for an item that somehow arrives without
  // one keeps a missing field from taking the row down.
  const mediaType =
    row.media_type === 'mixed'
      ? (item: RecommendedRow['items'][number]) => item.media_type ?? 'movie'
      : row.media_type

  return (
    <MediaRow
      title={row.title}
      qualifier={row.qualifier || undefined}
      items={row.items}
      mediaType={mediaType}
      expandHref={row.browse ? browseHref(row.browse) : undefined}
    />
  )
}

// What the page shows where its discovery rows will be, while the backend
// deals them: a few rows' worth of skeleton posters, not a blank gap.
const LOADING_ROWS = 4

// The fewest titles a row is worth showing with (the backend's own
// feed.MIN_ITEMS), after `alreadyShown` has taken its share.
const MIN_ITEMS = 6

/**
 * Every discovery row on a landing page, and the order the page runs in.
 *
 * The backend fills the rows and decides the order — per person, per UTC
 * day — so the same account sees the same page on its phone and laptop and
 * a reload doesn't redeal it. It also deals the titles, so nothing repeats
 * down the page (the rows used to be fetched here one by one, each a
 * popularity list, and opened with the same films). This only renders.
 */
export function useRecommendedRows(page: 'home' | 'movies' | 'tv') {
  const query = useQuery({
    queryKey: ['recommendations', page],
    queryFn: () => getRecommendations(page),
    // The answer only changes at midnight UTC (and as someone's history
    // grows, which is fine to catch on the next visit), so re-asking while
    // a tab is open buys nothing. Not retried: a failed fetch should cost
    // the rows rather than hammer the backend for them.
    staleTime: 30 * 60_000,
    retry: false,
  })

  const rows = query.data?.rows ?? []
  const layout = query.data?.layout ?? []

  /**
   * The page's rows in the backend's order: its own rows (`own`, keyed by
   * the names the layout uses — top10, continue, recent, requested,
   * subscribed) interleaved with the ones the backend filled.
   *
   * Tolerant of the two halves running a deploy apart: a layout naming a
   * row this build doesn't have is skipped, and an own row the layout
   * never mentioned keeps its declared position at the end. While the
   * rows are still coming, the own rows show in declared order with
   * skeleton rows after them.
   *
   * `alreadyShown` is what the page's own rows carry ("movie:603") —
   * Continue watching, New in your library. The backend deals its rows so
   * nothing repeats among them, but it doesn't hold those two lists (they
   * come from Plex, per person, uncached), so a film someone is half-way
   * through could still turn up again in a genre row. Left out here.
   */
  function order(own: Record<string, ReactNode>, alreadyShown: ReadonlySet<string> = new Set()): ReactNode[] {
    if (query.isLoading) {
      return [
        ...Object.values(own),
        ...Array.from({ length: LOADING_ROWS }, (_, i) => <MediaRow key={`loading-${i}`} title={'\u00a0'} items={[]} mediaType="movie" loading />),
      ]
    }
    const filled: Record<string, ReactNode> = {}
    for (const row of rows) {
      const items = row.items.filter((item) => !alreadyShown.has(`${item.media_type ?? row.media_type}:${item.id}`))
      if (items.length >= MIN_ITEMS) filled[row.key] = <RecommendedMediaRow key={row.key} row={{ ...row, items }} />
    }

    const all = { ...own, ...filled }
    const placed = new Set<string>()
    const out: ReactNode[] = []
    for (const key of layout) {
      if (all[key] && !placed.has(key)) {
        out.push(all[key])
        placed.add(key)
      }
    }
    for (const [key, node] of Object.entries(own)) {
      if (!placed.has(key)) out.push(node)
    }
    return out
  }

  return { order, isLoading: query.isLoading, isError: query.isError }
}
