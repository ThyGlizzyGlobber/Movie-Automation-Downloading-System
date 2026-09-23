import type { ReactNode } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { useRecommendedRows } from '../recommendations/useRecommendedRows'
import { getDiscoverByGenre, getDiscoverByProvider, getDiscoverPopular, getDiscoverTrending, getComingSoon } from '../../api/movies'
import HeroCarousel from '../../components/HeroCarousel'
import { getHeroSlides } from '../../api/hero'
import MediaRow from '../../components/MediaRow'
import TopTenRow from '../../components/TopTenRow'
import ProviderChips from '../../components/ProviderChips'
import ErrorState from '../../components/ErrorState'
import { usePageTitle } from '../../lib/chrome'
import { ROW_PROVIDERS } from '../../lib/providers'
import { browseHref } from '../../api/browse'


// Curated, row-based — no Popular/Trending/Coming Soon tabs; each row's
// title is the way to reach that one category's own full list
// (the browse page), Prime-Video-style. The hero carousel is the same
// HeroCarousel Home uses, movie-only here (see HomePage.tsx) — one
// component, not duplicated three times piecemeal.
const MOVIE_GENRES = [
  { id: 28, label: 'Action' },
  { id: 35, label: 'Comedies' },
  { id: 27, label: 'Horror' },
  { id: 10749, label: 'Romance' },
  { id: 16, label: 'Animation' },
  { id: 99, label: 'Documentaries' },
]

export default function MoviesLandingPage() {
  usePageTitle('Movies')

  const trending = useQuery({ queryKey: ['movies', 'trending'], queryFn: () => getDiscoverTrending(1) })
  const popular = useQuery({ queryKey: ['movies', 'popular'], queryFn: () => getDiscoverPopular(1) })
  const comingSoon = useQuery({ queryKey: ['movies', 'comingSoon'], queryFn: () => getComingSoon(1) })
  const genreResults = useQueries({
    queries: MOVIE_GENRES.map((g) => ({
      queryKey: ['movies', 'genre', g.id],
      queryFn: () => getDiscoverByGenre(g.id, 1),
    })),
  })
  const providerResults = useQueries({
    queries: ROW_PROVIDERS.map((p) => ({
      queryKey: ['movies', 'provider', p.id],
      queryFn: () => getDiscoverByProvider(p.id, 1),
    })),
  })

  // Enriched server-side in one call — see HomePage's own note.
  const hero = useQuery({ queryKey: ['hero', 'movies'], queryFn: () => getHeroSlides('movies') })
  const { order } = useRecommendedRows('movies')

  // Each section shows its own skeleton until its data arrives; the page
  // only gives way to an error once the main lists have all failed.
  const firstError = trending.error || popular.error || comingSoon.error
  if (firstError && !trending.data && !popular.data && !comingSoon.data) {
    return <ErrorState message={firstError instanceof Error ? firstError.message : undefined} />
  }

  // Ordered by the backend, same as Home — see useRecommendedRows. Only
  // the discovery rows take part: the "Browse by service" section and the
  // provider rows below it stay put, because that heading introduces the
  // rows under it and shuffling it away from them would leave it
  // introducing nothing.
  const ownRows: Record<string, ReactNode> = {
    top10: <TopTenRow key="top10" movies={trending.data?.results ?? []} shows={[]} only="movie" loading={trending.isLoading} />,
    trending: <MediaRow key="trending" title="Trending" qualifier="movies" items={trending.data?.results ?? []} mediaType="movie" loading={trending.isLoading} expandHref={browseHref({ type: 'movie', sort: 'trending' })} />,
    popular: <MediaRow key="popular" title="Popular" items={popular.data?.results ?? []} mediaType="movie" loading={popular.isLoading} expandHref={browseHref({ type: 'movie' })} />,
    'coming-soon': <MediaRow key="coming-soon" title="Coming" qualifier="soon" items={comingSoon.data?.results ?? []} mediaType="movie" loading={comingSoon.isLoading} expandHref={browseHref({ type: 'movie', list: 'coming-soon' })} />,
  }
  MOVIE_GENRES.forEach((g, i) => {
    ownRows[`genre:${g.id}`] = (
      <MediaRow
        key={g.id}
        title={g.label}
        items={genreResults[i].data?.results ?? []}
        mediaType="movie"
        loading={genreResults[i].isLoading}
        expandHref={browseHref({ type: 'movie', genre: g.id })}
      />
    )
  })
  const contentRows = order(ownRows)

  return (
    <>
      <HeroCarousel items={hero.data ?? []} loading={hero.isLoading} />
      {contentRows}
      <section className="row">
        <h2>
          Browse <span className="row-qualifier">by service</span>
        </h2>
        <ProviderChips type="movie" />
      </section>
      {ROW_PROVIDERS.map((p, i) => (
        <MediaRow
          key={p.id}
          title="Popular"
          qualifier={`on ${p.name}`}
          items={providerResults[i].data?.results ?? []}
          mediaType="movie"
          loading={providerResults[i].isLoading}
          expandHref={browseHref({ type: 'movie', provider: p.id })}
        />
      ))}
    </>
  )
}
