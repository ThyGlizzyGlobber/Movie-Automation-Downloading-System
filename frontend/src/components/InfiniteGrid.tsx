import { useEffect, useRef } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import PosterCard from './PosterCard'
import LoadingState from './LoadingState'
import ErrorState from './ErrorState'
import EmptyState from './EmptyState'
import type { TmdbListItem, TmdbListResponse } from '../types/movies'
import './InfiniteGrid.css'

// Auto-loads more pages as the user scrolls near the bottom, via an
// IntersectionObserver on an invisible sentinel — no "Load more" button.
// A short/wide viewport where the sentinel starts already on-screen
// self-corrects naturally: the observer fires immediately, which fetches
// the next page, which re-renders with the sentinel now further down,
// repeating until it's genuinely out of range — same end result as the
// old app's explicit "front-load enough pages to fill 4 rows" pass,
// without needing separate code for it.
export default function InfiniteGrid({
  queryKey,
  fetchPage,
  mediaType,
  emptyMessage,
  filterItem,
  onTotal,
}: {
  queryKey: unknown[]
  fetchPage: (page: number) => Promise<TmdbListResponse>
  mediaType: 'movie' | 'tv' | ((item: TmdbListItem) => 'movie' | 'tv')
  emptyMessage: string
  /* Client-side filter (the browse page's In Plex / Requested / Not yet
     switch). Pages keep loading while the sentinel stays in view, so a
     sparse filter fills in on its own. */
  filterItem?: (item: TmdbListItem) => boolean
  /* TMDB's total for the whole list, from the first page. */
  onTotal?: (total: number) => void
}) {
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: 1,
    getNextPageParam: (lastPage) => (lastPage.page < lastPage.total_pages ? lastPage.page + 1 : undefined),
  })

  const sentinelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || !query.hasNextPage) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !query.isFetchingNextPage) {
          query.fetchNextPage()
        }
      },
      { rootMargin: '800px 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.hasNextPage, query.isFetchingNextPage])

  const total = query.data?.pages[0]?.total_results
  useEffect(() => {
    if (onTotal && typeof total === 'number') onTotal(total)
  }, [onTotal, total])

  if (query.isLoading) return <LoadingState />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }

  // TMDB's own discover/provider results can repeat an item across
  // consecutive pages (confirmed live against the real API, provider-
  // filtered results especially) — deduped by id so React never sees two
  // children with the same key, rather than trusting each page to be a
  // disjoint slice.
  const seen = new Set<number>()
  const items = (query.data?.pages.flatMap((p) => p.results) ?? []).filter((item) => {
    if (seen.has(item.id)) return false
    seen.add(item.id)
    return filterItem ? filterItem(item) : true
  })
  if (!items.length && !query.hasNextPage) return <EmptyState message={emptyMessage} />

  return (
    <>
      <div className="grid category-grid">
        {items.map((item) => (
          <PosterCard key={item.id} item={item} mediaType={typeof mediaType === 'function' ? mediaType(item) : mediaType} />
        ))}
      </div>
      <div className="grid-sentinel" ref={sentinelRef}>
        {query.isFetchingNextPage && <div className="spinner" />}
      </div>
    </>
  )
}
