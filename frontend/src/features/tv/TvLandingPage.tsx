import { useQueries, useQuery } from '@tanstack/react-query'
import { getTvDiscoverByGenre, getTvDiscoverByProvider, getTvDiscoverPopular, getTvDiscoverTrending, getTvComingSoon } from '../../api/tv'
import MediaRow from '../../components/MediaRow'
import ProviderChips from '../../components/ProviderChips'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { ROW_PROVIDERS } from '../../lib/providers'

// Same curated, row-based treatment as MoviesLandingPage, TV-only. The
// old app's "Watching" preview rows (subscribed shows) aren't ported
// yet — that's the Watching/subscriptions feature, out of this step's
// scope (Movies/TV/detail/search/person pages).
const TV_GENRES = [
  { id: 35, label: 'Comedies' },
  { id: 18, label: 'Dramas' },
  { id: 10765, label: 'Sci-Fi & Fantasy' },
  { id: 16, label: 'Animation' },
  { id: 99, label: 'Documentaries' },
  { id: 80, label: 'Crime' },
  { id: 10764, label: 'Reality' },
]

export default function TvLandingPage() {
  usePageTitle('TV Shows')
  useSetHasHero(false)

  const trending = useQuery({ queryKey: ['tv', 'trending'], queryFn: () => getTvDiscoverTrending(1) })
  const popular = useQuery({ queryKey: ['tv', 'popular'], queryFn: () => getTvDiscoverPopular(1) })
  const comingSoon = useQuery({ queryKey: ['tv', 'comingSoon'], queryFn: () => getTvComingSoon(1) })
  const genreResults = useQueries({
    queries: TV_GENRES.map((g) => ({
      queryKey: ['tv', 'genre', g.id],
      queryFn: () => getTvDiscoverByGenre(g.id, 1),
    })),
  })
  const providerResults = useQueries({
    queries: ROW_PROVIDERS.map((p) => ({
      queryKey: ['tv', 'provider', p.id],
      queryFn: () => getTvDiscoverByProvider(p.id, 1),
    })),
  })

  if (trending.isLoading || popular.isLoading || comingSoon.isLoading) return <LoadingState />
  const firstError = trending.error || popular.error || comingSoon.error
  if (firstError) return <ErrorState message={firstError instanceof Error ? firstError.message : undefined} />

  return (
    <>
      <MediaRow title="Trending TV Shows" items={trending.data?.results ?? []} mediaType="tv" expandHref="/tv/trending" />
      <MediaRow title="Popular" items={popular.data?.results ?? []} mediaType="tv" expandHref="/tv/popular" />
      <MediaRow title="Coming Soon" items={comingSoon.data?.results ?? []} mediaType="tv" expandHref="/tv/coming-soon" />
      {TV_GENRES.map((g, i) => (
        <MediaRow
          key={g.id}
          title={g.label}
          items={genreResults[i].data?.results ?? []}
          mediaType="tv"
          expandHref={`/tv/genre/${g.id}/${encodeURIComponent(g.label)}`}
        />
      ))}
      <section className="row">
        <h2>Browse a Service</h2>
        <ProviderChips hrefPrefix="/tv/provider" />
      </section>
      {ROW_PROVIDERS.map((p, i) => (
        <MediaRow
          key={p.id}
          title={`Popular on ${p.name}`}
          items={providerResults[i].data?.results ?? []}
          mediaType="tv"
          expandHref={`/tv/provider/${p.id}/${encodeURIComponent(p.name)}`}
        />
      ))}
    </>
  )
}
