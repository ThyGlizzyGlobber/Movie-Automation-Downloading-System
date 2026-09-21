import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { searchMovies } from '../../api/movies'
import { searchTv } from '../../api/tv'
import PosterCard from '../../components/PosterCard'
import { PosterCardSkeleton } from '../../components/Skeleton'
import EmptyState from '../../components/EmptyState'
import { usePageTitle } from '../../lib/chrome'
import { pickBestMediaType, type SearchMediaType } from '../../lib/searchRank'

// Unified search — movies and TV shows in parallel, one side shown at a
// time behind the same segmented toggle the Top 10 row uses, rather than
// the two stacked sections this used to be. A title belongs to one of the
// two, and stacking them meant a search for a series opened on a screen
// of unrelated films: "law and order" led with four obscure westerns,
// with the show the household actually wanted a full page below.
//
// Which side opens is a guess from the results themselves (see
// lib/searchRank), and only a guess — it picks the tab, it never hides
// the other one, and the moment someone chooses for themselves their
// choice sticks until the query changes.
//
// Each side still swallows its own fetch failure into an empty result
// (mirrors the old app's `.catch(() => [])`) so one endpoint being down
// doesn't blank the whole page. A search returns up to 20 titles a side;
// a full page is the usual case.
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
  // Both, not either: the guess compares the two sides, so deciding
  // before the slower one lands would show a tab that then moved under
  // the reader. The two requests are in flight together, so the wait is
  // the slower of them rather than the sum.
  const loading = moviesQuery.isLoading || showsQuery.isLoading

  // Null until someone picks a tab themselves, and back to null on a new
  // query — so the guess drives the page, their choice overrides it, and
  // the next search gets its own guess rather than inheriting this one.
  const [chosen, setChosen] = useState<SearchMediaType | null>(null)
  useEffect(() => setChosen(null), [query])

  const active: SearchMediaType = chosen ?? (loading ? 'movie' : pickBestMediaType(movies, shows))
  const items = active === 'tv' ? shows : movies
  const otherHasResults = (active === 'tv' ? movies : shows).length > 0

  if (!loading && !movies.length && !shows.length) {
    return <EmptyState message={`Nothing matched “${query}”.`} />
  }

  return (
    <section className="row">
      <div className="row-head">
        <h2>
          Results <span className="row-qualifier">for “{query}”</span>
        </h2>
        <div className="row-head-right">
          <div className="seg" role="tablist" aria-label="Result type">
            <button
              role="tab"
              aria-selected={active === 'movie'}
              className={active === 'movie' ? 'active' : ''}
              onClick={() => setChosen('movie')}
            >
              Movies
            </button>
            <button
              role="tab"
              aria-selected={active === 'tv'}
              className={active === 'tv' ? 'active' : ''}
              onClick={() => setChosen('tv')}
            >
              TV Shows
            </button>
          </div>
        </div>
      </div>
      {loading ? (
        <SearchGridSkeleton />
      ) : items.length ? (
        <div className="grid">
          {items.map((item) => (
            <PosterCard key={item.id} item={item} mediaType={active} />
          ))}
        </div>
      ) : (
        // Points at the tab that does have something rather than reading
        // as a dead end, since the other side is one click away and the
        // toggle alone doesn't say which of the two is empty.
        <EmptyState
          message={
            otherHasResults
              ? active === 'tv'
                ? 'No TV shows matched — try Movies.'
                : 'No movies matched — try TV Shows.'
              : active === 'tv'
                ? 'No TV shows matched.'
                : 'No movies matched.'
          }
        />
      )}
    </section>
  )
}
