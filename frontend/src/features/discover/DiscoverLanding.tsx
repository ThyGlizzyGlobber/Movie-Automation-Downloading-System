import type { ReactNode } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { useRecommendedRows } from '../recommendations/useRecommendedRows'
import HeroCarousel from '../../components/HeroCarousel'
import { getHeroSlides } from '../../api/hero'
import MediaRow from '../../components/MediaRow'
import TopTenRow from '../../components/TopTenRow'
import ProviderChips from '../../components/ProviderChips'
import ErrorState from '../../components/ErrorState'
import { usePageTitle } from '../../lib/chrome'
import { ROW_PROVIDERS } from '../../lib/providers'
import { browseHref } from '../../api/browse'
import type { TmdbListResponse } from '../../types/movies'

// What sets the Movies page apart from the TV one: its endpoints, its
// genre rows and a word or two of copy. Everything else is one page.
export interface DiscoverSource {
  /* Query-key prefix, hero kind and recommendations page in one. */
  page: 'movies' | 'tv'
  mediaType: 'movie' | 'tv'
  title: string
  trendingQualifier: string
  genres: { id: number; label: string }[]
  trending: (page: number) => Promise<TmdbListResponse>
  popular: (page: number) => Promise<TmdbListResponse>
  comingSoon: (page: number) => Promise<TmdbListResponse>
  byGenre: (genreId: number, page: number) => Promise<TmdbListResponse>
  byProvider: (providerId: number, page: number) => Promise<TmdbListResponse>
}

// Curated, row-based — no Popular/Trending/Coming Soon tabs; each row's
// title is the way to reach that one category's own full list
// (the browse page), Prime-Video-style. The hero carousel is the same
// HeroCarousel Home uses, one half of the catalogue here (see
// HomePage.tsx) — one component, not duplicated three times piecemeal.
export default function DiscoverLanding({
  source,
  extraRows,
}: {
  source: DiscoverSource
  /* The page's own rows beyond the shared ones, dealt in right after
     the Top 10. */
  extraRows?: Record<string, ReactNode>
}) {
  const { page, mediaType, genres } = source
  usePageTitle(source.title)

  const trending = useQuery({ queryKey: [page, 'trending'], queryFn: () => source.trending(1) })
  const popular = useQuery({ queryKey: [page, 'popular'], queryFn: () => source.popular(1) })
  const comingSoon = useQuery({ queryKey: [page, 'comingSoon'], queryFn: () => source.comingSoon(1) })
  const genreResults = useQueries({
    queries: genres.map((g) => ({
      queryKey: [page, 'genre', g.id],
      queryFn: () => source.byGenre(g.id, 1),
    })),
  })
  const providerResults = useQueries({
    queries: ROW_PROVIDERS.map((p) => ({
      queryKey: [page, 'provider', p.id],
      queryFn: () => source.byProvider(p.id, 1),
    })),
  })

  // Enriched server-side in one call — see HomePage's own note.
  const hero = useQuery({ queryKey: ['hero', page], queryFn: () => getHeroSlides(page) })
  const { order } = useRecommendedRows(page, [
    ...genres.map((g) => `genre:${g.id}`),
    ...ROW_PROVIDERS.map((p) => `provider:${p.id}`),
  ])

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
  const topTen = trending.data?.results ?? []
  const ownRows: Record<string, ReactNode> = {
    top10: <TopTenRow key="top10" movies={mediaType === 'movie' ? topTen : []} shows={mediaType === 'tv' ? topTen : []} only={mediaType} loading={trending.isLoading} />,
    ...extraRows,
    trending: <MediaRow key="trending" title="Trending" qualifier={source.trendingQualifier} items={trending.data?.results ?? []} mediaType={mediaType} loading={trending.isLoading} expandHref={browseHref({ type: mediaType, sort: 'trending' })} />,
    popular: <MediaRow key="popular" title="Popular" items={popular.data?.results ?? []} mediaType={mediaType} loading={popular.isLoading} expandHref={browseHref({ type: mediaType })} />,
    'coming-soon': <MediaRow key="coming-soon" title="Coming" qualifier="soon" items={comingSoon.data?.results ?? []} mediaType={mediaType} loading={comingSoon.isLoading} expandHref={browseHref({ type: mediaType, list: 'coming-soon' })} />,
  }
  genres.forEach((g, i) => {
    ownRows[`genre:${g.id}`] = (
      <MediaRow
        key={g.id}
        title={g.label}
        items={genreResults[i].data?.results ?? []}
        mediaType={mediaType}
        loading={genreResults[i].isLoading}
        expandHref={browseHref({ type: mediaType, genre: g.id })}
      />
    )
  })
  ROW_PROVIDERS.forEach((provider, i) => {
    ownRows[`provider:${provider.id}`] = (
      <MediaRow
        key={provider.id}
        title="Popular"
        qualifier={`on ${provider.name}`}
        items={providerResults[i].data?.results ?? []}
        mediaType={mediaType}
        loading={providerResults[i].isLoading}
        expandHref={browseHref({ type: mediaType, provider: provider.id })}
      />
    )
  })
  const contentRows = order(ownRows)

  return (
    <>
      <HeroCarousel items={hero.data ?? []} loading={hero.isLoading} />
      {contentRows}
      {/* Last, always: the service icons are a way out of the page rather
          than another row of it, and dealing them into the middle would
          interrupt the browsing they exist to follow. */}
      <section className="row">
        <h2>
          Browse <span className="row-qualifier">by service</span>
        </h2>
        <ProviderChips type={mediaType} />
      </section>
    </>
  )
}
