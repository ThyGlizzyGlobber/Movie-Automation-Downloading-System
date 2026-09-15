import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import {
  getDiscoverByGenre,
  getDiscoverPopular,
  getDiscoverTrending,
  getComingSoon,
} from '../../api/movies'
import { getTvDiscoverByGenre, getTvDiscoverPopular, getTvDiscoverTrending, listShows } from '../../api/tv'
import HeroCarousel from '../../components/HeroCarousel'
import MediaRow from '../../components/MediaRow'
import TopTenRow from '../../components/TopTenRow'
import ProviderChips from '../../components/ProviderChips'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { mixTrending, tagMediaType } from '../../lib/homeHero'

const HERO_SLIDE_COUNT = 5

// Same 5 named genres as the old app's own HOME_GENRES — each pairs a
// label with both a movie and a TV genre id (they diverge, e.g. Sci-Fi
// is 878 for movies but 10765 for TV), interleaved into one mixed row.
const HOME_GENRES = [
  { label: 'Comedies', movieId: 35, tvId: 35 },
  { label: 'Action & Adventure', movieId: 28, tvId: 10759 },
  { label: 'Sci-Fi & Fantasy', movieId: 878, tvId: 10765 },
  { label: 'Animation', movieId: 16, tvId: 16 },
  { label: 'Documentaries', movieId: 99, tvId: 99 },
]

// The merged Home feed — mixes movies and TV into one curated, row-based
// landing page, alongside (not instead of) the separate movie-only/
// TV-only Movies/TV pages. No subnav — straight into the hero + rows.
export default function HomePage() {
  usePageTitle(null)
  useSetHasHero(true)

  const movieTrending = useQuery({ queryKey: ['movies', 'trending'], queryFn: () => getDiscoverTrending(1) })
  const tvTrending = useQuery({ queryKey: ['tv', 'trending'], queryFn: () => getTvDiscoverTrending(1) })
  const moviePopular = useQuery({ queryKey: ['movies', 'popular'], queryFn: () => getDiscoverPopular(1) })
  const tvPopular = useQuery({ queryKey: ['tv', 'popular'], queryFn: () => getTvDiscoverPopular(1) })
  const comingSoon = useQuery({ queryKey: ['movies', 'comingSoon'], queryFn: () => getComingSoon(1) })
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => listShows() })
  const genreMovieResults = useQueries({
    queries: HOME_GENRES.map((g) => ({ queryKey: ['movies', 'genre', g.movieId], queryFn: () => getDiscoverByGenre(g.movieId, 1) })),
  })
  const genreTvResults = useQueries({
    queries: HOME_GENRES.map((g) => ({ queryKey: ['tv', 'genre', g.tvId], queryFn: () => getTvDiscoverByGenre(g.tvId, 1) })),
  })

  const mixedTrending = useMemo(
    () => mixTrending(movieTrending.data?.results ?? [], tvTrending.data?.results ?? []),
    [movieTrending.data, tvTrending.data],
  )
  // A title with no backdrop art isn't usable as a hero slide — that
  // filter runs first, then the top HERO_SLIDE_COUNT off the front of
  // the same already-popularity-sorted list the row below uses.
  const heroItems = useMemo(() => mixedTrending.filter((it) => it.backdrop_path).slice(0, HERO_SLIDE_COUNT), [mixedTrending])

  // Sorted by most recently subscribed — see TvLandingPage's own note on
  // why the old app's richer "New In Watching" (sorted by most recently
  // *aired* episode) isn't ported: it needs a per-show TMDB enrichment
  // fetch that isn't worth adding twice over piecemeal.
  const subscribedShows = useMemo(
    () =>
      (shows.data ?? [])
        .slice()
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .map((s) => ({ id: s.tmdb_id, name: s.title, poster_path: s.poster_path, mediaType: 'tv' as const })),
    [shows.data],
  )
  // Scattered at a random point among the other rows on every load
  // (closer to Netflix's own "Continue Watching" placement than always
  // pinned to the top) — computed once per mount via useMemo's own
  // dependency-array stability, not re-rolled on every render.
  const watchingRowPosition = useMemo(() => Math.floor(Math.random() * (HOME_GENRES.length + 4)), [])

  if (movieTrending.isLoading || tvTrending.isLoading || moviePopular.isLoading || tvPopular.isLoading || comingSoon.isLoading) {
    return <LoadingState />
  }
  const firstError = movieTrending.error || tvTrending.error || moviePopular.error || tvPopular.error || comingSoon.error
  if (firstError) return <ErrorState message={firstError instanceof Error ? firstError.message : undefined} />

  const genreRows = HOME_GENRES.map((g, i) => {
    const movieItems = tagMediaType(genreMovieResults[i].data?.results ?? [], 'movie')
    const tvItems = tagMediaType(genreTvResults[i].data?.results ?? [], 'tv')
    const merged: typeof movieItems = []
    for (let j = 0; j < Math.max(movieItems.length, tvItems.length); j++) {
      if (movieItems[j]) merged.push(movieItems[j])
      if (tvItems[j]) merged.push(tvItems[j])
    }
    return <MediaRow key={g.label} title={g.label} items={merged} mediaType={(it) => it.mediaType} />
  })

  const contentRows = [
    // Top 10 leads, straight under the hero (the reference's order).
    <TopTenRow key="top10" movies={movieTrending.data?.results ?? []} shows={tvTrending.data?.results ?? []} />,
    <MediaRow key="trending" title="Trending Now" items={mixedTrending} mediaType={(it) => it.mediaType} />,
    <MediaRow key="popular-movies" title="Popular Movies" items={moviePopular.data?.results ?? []} mediaType="movie" expandHref="/movies/popular" />,
    <MediaRow key="popular-tv" title="Popular TV Shows" items={tvPopular.data?.results ?? []} mediaType="tv" expandHref="/tv/popular" />,
    <MediaRow key="new-releases" title="New Releases" items={comingSoon.data?.results ?? []} mediaType="movie" expandHref="/movies/coming-soon" />,
    ...genreRows,
  ]
  if (subscribedShows.length) {
    contentRows.splice(
      Math.min(watchingRowPosition, contentRows.length),
      0,
      <MediaRow key="subscribed" title="Subscribed Shows" items={subscribedShows} mediaType="tv" expandHref="/tv/watching" />,
    )
  }

  return (
    <>
      <HeroCarousel items={heroItems} />
      {contentRows}
      <section className="row">
        <h2>Browse a Service</h2>
        <ProviderChips hrefPrefix="/movies/provider" />
      </section>
    </>
  )
}
