import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { searchMovies } from '../../api/movies'
import { searchTv } from '../../api/tv'
import PosterCard from '../../components/PosterCard'
import { PosterCardSkeleton } from '../../components/Skeleton'
import EmptyState from '../../components/EmptyState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'

// Unified search — movies and TV shows in parallel, each independently
// empty-stated rather than one combined list, since a title only ever
// really belongs to one of the two rows a user is scanning. Each side
// swallows its own fetch failure into an empty result (mirrors the old
// app's `.catch(() => [])`) so one endpoint being down doesn't blank the
// whole page — it just shows that section as empty. Each section shows
// skeleton cards until its own results arrive.
// A search returns up to 20 titles a side; a full page is the usual case.
const SEARCH_SKELETONS = 20

function SearchGridSkeleton() {
  return (
    <div className="grid" aria-busy="true">
      {Array.from({ length: SEARCH_SKELETONS }, (_, i) => (
        <PosterCardSkeleton key={i} />
      ))}
    </div>
  )
}

export default function SearchPage() {
  const { query: rawQuery } = useParams()
  const query = rawQuery ? decodeURIComponent(rawQuery) : ''
  usePageTitle(`“${query}”`)
  useSetHasHero(false)

  const moviesQuery = useQuery({
    queryKey: ['search', 'movie', query],
    queryFn: () => searchMovies(query).catch(() => []),
    enabled: query.length > 0,
  })
  const showsQuery = useQuery({
    queryKey: ['search', 'tv', query],
    queryFn: () => searchTv(query).catch(() => []),
    enabled: query.length > 0,
  })

  const movies = moviesQuery.data ?? []
  const shows = showsQuery.data ?? []
  const loading = moviesQuery.isLoading || showsQuery.isLoading

  if (!loading && !movies.length && !shows.length) {
    return <EmptyState message={`Nothing matched “${query}”.`} />
  }

  return (
    <>
      <section className="row">
        <h2>Movies</h2>
        {moviesQuery.isLoading ? (
          <SearchGridSkeleton />
        ) : movies.length ? (
          <div className="grid">
            {movies.map((item) => (
              <PosterCard key={item.id} item={item} mediaType="movie" />
            ))}
          </div>
        ) : (
          <EmptyState message="No movies matched." />
        )}
      </section>
      <section className="row">
        <h2>TV Shows</h2>
        {showsQuery.isLoading ? (
          <SearchGridSkeleton />
        ) : shows.length ? (
          <div className="grid">
            {shows.map((item) => (
              <PosterCard key={item.id} item={item} mediaType="tv" />
            ))}
          </div>
        ) : (
          <EmptyState message="No TV shows matched." />
        )}
      </section>
    </>
  )
}
