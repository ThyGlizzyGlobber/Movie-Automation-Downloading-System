import InfiniteGrid from './InfiniteGrid'
import { usePageTitle, useSetHasHero } from '../lib/chrome'
import type { TmdbListResponse } from '../types/movies'

// One category's full, paginated list — the row-title-click destination
// for Trending/Popular/New Releases/genre rows on Movies/TV.
export default function CategoryPage({
  title,
  mediaType,
  fetchPage,
  queryKey,
}: {
  title: string
  mediaType: 'movie' | 'tv'
  fetchPage: (page: number) => Promise<TmdbListResponse>
  queryKey: unknown[]
}) {
  usePageTitle(title)
  useSetHasHero(false)
  return (
    <>
      <h1 className="page-title">{title}</h1>
      <InfiniteGrid queryKey={queryKey} fetchPage={fetchPage} mediaType={mediaType} emptyMessage="Nothing found." />
    </>
  )
}
