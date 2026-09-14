import { useState } from 'react'
import { useParams } from 'react-router-dom'
import InfiniteGrid from './InfiniteGrid'
import PosterCard, { type PosterCardItem } from './PosterCard'
import LoadingState from './LoadingState'
import ErrorState from './ErrorState'
import EmptyState from './EmptyState'
import { usePageTitle, useSetHasHero } from '../lib/chrome'
import type { TmdbListItem, TmdbListResponse } from '../types/movies'
import './ProviderPage.css'

export default function ProviderPage({
  mediaType,
  discoverByProvider,
  search,
}: {
  mediaType: 'movie' | 'tv'
  discoverByProvider: (providerId: number, page: number) => Promise<TmdbListResponse>
  search: (query: string, providerId?: number | null) => Promise<TmdbListItem[]>
}) {
  const { id, name } = useParams()
  const providerId = Number(id)
  const providerName = name ? decodeURIComponent(name) : 'this service'
  usePageTitle(providerName)
  useSetHasHero(false)

  const [query, setQuery] = useState('')
  const [searchState, setSearchState] = useState<
    { status: 'idle' } | { status: 'loading' } | { status: 'error'; message?: string } | { status: 'done'; results: PosterCardItem[] }
  >({ status: 'idle' })

  async function submitSearch() {
    const q = query.trim()
    if (!q) {
      setSearchState({ status: 'idle' })
      return
    }
    setSearchState({ status: 'loading' })
    try {
      const results = await search(q, providerId)
      setSearchState({ status: 'done', results })
    } catch (err) {
      setSearchState({ status: 'error', message: err instanceof Error ? err.message : undefined })
    }
  }

  return (
    <>
      <input
        type="search"
        className="inline-search"
        placeholder={`Search within ${providerName}…`}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submitSearch()
        }}
      />
      {searchState.status === 'idle' && (
        <InfiniteGrid
          queryKey={[mediaType, 'provider', providerId]}
          fetchPage={(page) => discoverByProvider(providerId, page)}
          mediaType={mediaType}
          emptyMessage={`No titles found for ${providerName}.`}
        />
      )}
      {searchState.status === 'loading' && <LoadingState />}
      {searchState.status === 'error' && <ErrorState message={searchState.message} />}
      {searchState.status === 'done' &&
        (searchState.results.length ? (
          <div className="grid">
            {searchState.results.map((item) => (
              <PosterCard key={item.id} item={item} mediaType={mediaType} />
            ))}
          </div>
        ) : (
          <EmptyState message={`No titles on ${providerName} matched "${query}".`} />
        ))}
    </>
  )
}
