import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { getTvDiscoverByGenre, getTvDiscoverByProvider, getTvDiscoverPopular, getTvDiscoverTrending, getTvComingSoon, listShows } from '../../api/tv'
import HeroCarousel from '../../components/HeroCarousel'
import MediaRow from '../../components/MediaRow'
import ProviderChips from '../../components/ProviderChips'
import { PosterCardSkeleton } from '../../components/Skeleton'
import ErrorState from '../../components/ErrorState'
import { usePageTitle } from '../../lib/chrome'
import { ROW_PROVIDERS } from '../../lib/providers'
import { tagMediaType } from '../../lib/homeHero'
import { browseHref } from '../../api/browse'

const HERO_SLIDE_COUNT = 5

// Same curated, row-based treatment as MoviesLandingPage, TV-only —
// including the same HeroCarousel Home uses (see HomePage.tsx).
//
// The old app's "New In Watching" row (subscribed shows sorted by most
// recently *aired* episode) is a deliberate, documented gap here — it
// needs an extra per-show TMDB enrichment fetch (last_episode_to_air)
// that only paid for itself on Home/TV pages together. "Subscribed
// Shows" (sorted by most recently *subscribed*, below) needs no such
// fetch — ShowOut already carries poster_path — so it's the one ported.
const TV_GENRES = [
  { id: 35, label: 'Comedies' },
  { id: 18, label: 'Dramas' },
  { id: 10765, label: 'Sci-Fi & Fantasy' },
  { id: 16, label: 'Animation' },
  { id: 99, label: 'Documentaries' },
  { id: 80, label: 'Crime' },
  { id: 10764, label: 'Reality' },
]

// Followed shows carry no year or genre, so their cards have no meta line.
const followedSkeleton = () => <PosterCardSkeleton meta={false} />

export default function TvLandingPage() {
  usePageTitle('TV Shows')

  const shows = useQuery({ queryKey: ['shows'], queryFn: () => listShows() })
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

  const heroItems = useMemo(
    () => tagMediaType(trending.data?.results ?? [], 'tv').filter((it) => it.backdrop_path).slice(0, HERO_SLIDE_COUNT),
    [trending.data],
  )

  // Each section shows its own skeleton until its data arrives; the page
  // only gives way to an error once the main lists have all failed.
  const firstError = trending.error || popular.error || comingSoon.error
  if (firstError && !trending.data && !popular.data && !comingSoon.data) {
    return <ErrorState message={firstError instanceof Error ? firstError.message : undefined} />
  }

  // Most-recently-subscribed first — not blocking/critical, so a failed
  // fetch here just means an empty (hidden) row rather than an error page.
  // Series still going only: a finished show drops off once its last
  // check records it as ended or cancelled.
  const subscribedShows = (shows.data ?? [])
    .filter((s) => s.status === 'watching' && s.tmdb_status !== 'Ended' && s.tmdb_status !== 'Canceled')
    .slice()
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .map((s) => ({ id: s.tmdb_id, name: s.title, poster_path: s.poster_path }))

  return (
    <>
      <HeroCarousel items={heroItems} loading={trending.isLoading} />
      <MediaRow title="Shows" qualifier="you follow" items={subscribedShows} mediaType="tv" loading={shows.isLoading} renderSkeleton={followedSkeleton} expandHref="/tv/watching" />
      <MediaRow title="Trending" qualifier="shows" items={trending.data?.results ?? []} mediaType="tv" loading={trending.isLoading} expandHref={browseHref({ type: 'tv', sort: 'trending' })} />
      <MediaRow title="Popular" items={popular.data?.results ?? []} mediaType="tv" loading={popular.isLoading} expandHref={browseHref({ type: 'tv' })} />
      <MediaRow title="Coming" qualifier="soon" items={comingSoon.data?.results ?? []} mediaType="tv" loading={comingSoon.isLoading} expandHref={browseHref({ type: 'tv', list: 'coming-soon' })} />
      {TV_GENRES.map((g, i) => (
        <MediaRow
          key={g.id}
          title={g.label}
          items={genreResults[i].data?.results ?? []}
          mediaType="tv"
          loading={genreResults[i].isLoading}
          expandHref={browseHref({ type: 'tv', genre: g.id })}
        />
      ))}
      <section className="row">
        <h2>
          Browse <span className="row-qualifier">by service</span>
        </h2>
        <ProviderChips type="tv" />
      </section>
      {ROW_PROVIDERS.map((p, i) => (
        <MediaRow
          key={p.id}
          title="Popular"
          qualifier={`on ${p.name}`}
          items={providerResults[i].data?.results ?? []}
          mediaType="tv"
          loading={providerResults[i].isLoading}
          expandHref={browseHref({ type: 'tv', provider: p.id })}
        />
      ))}
    </>
  )
}
