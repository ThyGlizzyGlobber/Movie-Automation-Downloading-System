import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRecommendedRows } from '../recommendations/useRecommendedRows'
import HeroCarousel from '../../components/media/HeroCarousel'
import { getHeroSlides } from '../../api/hero'
import TopTenRow from '../../components/media/TopTenRow'
import ProviderChips from '../../components/media/ProviderChips'
import ErrorState from '../../components/ui/ErrorState'
import { usePageTitle } from '../../lib/chrome'
import type { TmdbListResponse } from '../../types/movies'

// What sets the Movies page apart from the TV one. Its genre and service
// rows are the backend's now (backend/app/api/recommendations.py), filled
// and dealt with the rest; the page itself only needs its trending list,
// for the Top 10.
export interface DiscoverSource {
  /* Query-key prefix, hero kind and recommendations page in one. */
  page: 'movies' | 'tv'
  mediaType: 'movie' | 'tv'
  title: string
  trending: (page: number) => Promise<TmdbListResponse>
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
  const { page, mediaType } = source
  usePageTitle(source.title)

  // For the Top 10 only — the Trending row itself, Popular, Coming soon,
  // the genre and service rows are the backend's (see useRecommendedRows).
  const trending = useQuery({ queryKey: [page, 'trending'], queryFn: () => source.trending(1) })

  // Enriched server-side in one call — see HomePage's own note.
  const hero = useQuery({ queryKey: ['hero', page], queryFn: () => getHeroSlides(page) })
  const recommended = useRecommendedRows(page)

  // Each section shows its own skeleton until its data arrives; the page
  // only gives way to an error once both the trending list and the rows
  // have failed.
  if (trending.error && !trending.data && recommended.isError) {
    return <ErrorState message={trending.error instanceof Error ? trending.error.message : undefined} />
  }

  // Ordered by the backend, same as Home — see useRecommendedRows. The
  // "Browse by service" section stays last, below.
  const topTen = trending.data?.results ?? []
  const ownRows: Record<string, ReactNode> = {
    top10: <TopTenRow key="top10" movies={mediaType === 'movie' ? topTen : []} shows={mediaType === 'tv' ? topTen : []} only={mediaType} loading={trending.isLoading} />,
    ...extraRows,
  }
  const contentRows = recommended.order(ownRows)

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
