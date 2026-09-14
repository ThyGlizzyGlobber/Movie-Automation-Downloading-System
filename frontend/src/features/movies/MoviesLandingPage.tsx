import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { getDiscoverByGenre, getDiscoverByProvider, getDiscoverPopular, getDiscoverTrending, getComingSoon } from '../../api/movies'
import HeroCarousel from '../../components/HeroCarousel'
import MediaRow from '../../components/MediaRow'
import ProviderChips from '../../components/ProviderChips'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { ROW_PROVIDERS } from '../../lib/providers'
import { tagMediaType } from '../../lib/homeHero'

const HERO_SLIDE_COUNT = 5

// Curated, row-based — no Popular/Trending/Coming Soon tabs; each row's
// title is the way to reach that one category's own full list
// (CategoryPage), Prime-Video-style. The hero carousel is the same
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
  useSetHasHero(true)

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

  const heroItems = useMemo(
    () => tagMediaType(trending.data?.results ?? [], 'movie').filter((it) => it.backdrop_path).slice(0, HERO_SLIDE_COUNT),
    [trending.data],
  )

  if (trending.isLoading || popular.isLoading || comingSoon.isLoading) return <LoadingState />
  const firstError = trending.error || popular.error || comingSoon.error
  if (firstError) return <ErrorState message={firstError instanceof Error ? firstError.message : undefined} />

  return (
    <>
      <HeroCarousel items={heroItems} />
      <MediaRow title="Trending Movies" items={trending.data?.results ?? []} mediaType="movie" expandHref="/movies/trending" />
      <MediaRow title="Popular" items={popular.data?.results ?? []} mediaType="movie" expandHref="/movies/popular" />
      <MediaRow title="New Releases" items={comingSoon.data?.results ?? []} mediaType="movie" expandHref="/movies/coming-soon" />
      {MOVIE_GENRES.map((g, i) => (
        <MediaRow
          key={g.id}
          title={g.label}
          items={genreResults[i].data?.results ?? []}
          mediaType="movie"
          expandHref={`/movies/genre/${g.id}/${encodeURIComponent(g.label)}`}
        />
      ))}
      <section className="row">
        <h2>Browse a Service</h2>
        <ProviderChips hrefPrefix="/movies/provider" />
      </section>
      {ROW_PROVIDERS.map((p, i) => (
        <MediaRow
          key={p.id}
          title={`Popular on ${p.name}`}
          items={providerResults[i].data?.results ?? []}
          mediaType="movie"
          expandHref={`/movies/provider/${p.id}/${encodeURIComponent(p.name)}`}
        />
      ))}
    </>
  )
}
