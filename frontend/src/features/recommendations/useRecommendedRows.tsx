import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getRecommendations } from '../../api/recommendations'
import MediaRow from '../../components/MediaRow'
import type { RecommendedRow } from '../../types/recommendations'

/** A row the backend built: "Because you watched…", "Today's top picks",
 *  or one of the named ones ("Comedies That Go Somewhere Dark"). */
function RecommendedMediaRow({ row }: { row: RecommendedRow }) {
  // "mixed" is the blended picks row, the one place films and shows sit
  // together; each item carries its own type there. Falling back to movie
  // for an item that somehow arrives without one keeps a missing field from
  // taking the row down.
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
    />
  )
}

/**
 * This person's rows for today, plus the order the whole page runs in.
 *
 * The order is the backend's, not this component's, and that is the point:
 * it is dealt once per person per UTC day, so the same account sees the same
 * page on its phone and its laptop, and a reload doesn't redeal it. Doing it
 * here instead would mean a different page per device and a new one on every
 * refresh — which is not freshness, it's noise.
 */
export function useRecommendedRows(page: 'home' | 'movies' | 'tv') {
  const query = useQuery({
    queryKey: ['recommendations', page],
    queryFn: () => getRecommendations(page),
    // The answer only changes at midnight UTC, so re-asking while a tab is
    // open buys nothing. Errors aren't retried: these rows decorate a page
    // that works without them, and a failed fetch should cost the
    // decoration rather than hammer the backend for it.
    staleTime: 30 * 60_000,
    retry: false,
  })

  const rows = query.data?.rows ?? []
  const layout = query.data?.layout ?? []

  /**
   * Interleave the page's own rows with the personalised ones.
   *
   * `own` is keyed by the same names the backend uses. Two kinds of
   * mismatch are tolerated on purpose, because the backend live-reloads
   * while the frontend needs a rebuild, so the halves routinely run a few
   * minutes apart: a layout naming a row this build doesn't have is
   * skipped, and a row this build has that the layout never mentioned —
   * the genre and provider rows, whose keys are ids the backend has no
   * reason to know — keeps its declared position at the end. Neither can
   * empty the page.
   */
  function order(own: Record<string, ReactNode>): ReactNode[] {
    const personalised: Record<string, ReactNode> = {}
    for (const row of rows) personalised[row.key] = <RecommendedMediaRow key={row.key} row={row} />

    const all = { ...own, ...personalised }
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

  return { order, isLoading: query.isLoading }
}
