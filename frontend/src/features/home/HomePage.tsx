import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRecommendedRows } from '../recommendations/useRecommendedRows'
import { getDiscoverTrending } from '../../api/movies'
import { getTvDiscoverTrending, listShows } from '../../api/tv'
import { getOnDeck, getRecentlyAdded } from '../../api/plex'
import HeroCarousel from '../../components/media/HeroCarousel'
import { getHeroSlides } from '../../api/hero'
import MediaRow from '../../components/media/MediaRow'
import TopTenRow from '../../components/media/TopTenRow'
import ContinueWatchingRow, { ContinueWatchingSkeleton } from './ContinueWatchingRow'
import RecentlyAddedRow from './RecentlyAddedRow'
import RequestedRow from './RequestedRow'
import ProviderChips from '../../components/media/ProviderChips'
import { PosterCardSkeleton } from '../../components/ui/Skeleton'
import ErrorState from '../../components/ui/ErrorState'
import { usePageTitle } from '../../lib/chrome'
import { browseHref } from '../../api/browse'

// The merged Home feed — mixes movies and TV into one curated, row-based
// landing page, alongside (not instead of) the separate movie-only/
// TV-only Movies/TV pages. No subnav — straight into the hero + rows.
// Home's first screens before anything is known, for the app's start-up
// skeleton: the same sections the page draws while they load.
export function HomeSkeleton() {
  return (
    <>
      <HeroCarousel items={[]} loading />
      <TopTenRow movies={[]} shows={[]} loading />
      <ContinueWatchingSkeleton />
      <MediaRow title="New" qualifier="in your library" items={[]} mediaType="movie" loading />
      <MediaRow title="On the way" qualifier="for the household" items={[]} mediaType="movie" loading expandHref="/requests" seeAllLabel="All requests" />
      <MediaRow title="Trending" qualifier="now" items={[]} mediaType="movie" loading expandHref={browseHref({ type: 'movie', sort: 'trending' })} />
    </>
  )
}

// Followed shows carry no year or genre, so their cards have no meta line.
const followedSkeleton = () => <PosterCardSkeleton meta={false} />

export default function HomePage() {
  usePageTitle(null)

  // Trending feeds the Top 10 row only; every other discovery row —
  // Trending, Popular, Coming soon, the genres, the personal and named
  // rows — is filled by the backend, ranked for this person and dealt so
  // nothing repeats (see useRecommendedRows).
  const movieTrending = useQuery({ queryKey: ['movies', 'trending'], queryFn: () => getDiscoverTrending(1) })
  const tvTrending = useQuery({ queryKey: ['tv', 'trending'], queryFn: () => getTvDiscoverTrending(1) })
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => listShows() })
  // The same queries Continue watching and New in your library run (same
  // keys, so one fetch between them), read here so the discovery rows can
  // leave out what those two already show.
  const onDeck = useQuery({ queryKey: ['plex-on-deck'], queryFn: getOnDeck, staleTime: 60_000 })
  const recentlyAdded = useQuery({ queryKey: ['plex-recently-added'], queryFn: getRecentlyAdded, staleTime: 120_000 })
  const alreadyShown = useMemo(
    () =>
      new Set(
        [...(onDeck.data?.items ?? []), ...(recentlyAdded.data?.items ?? [])]
          .filter((item) => item.tmdb_id)
          .map((item) => `${item.media_type}:${item.tmdb_id}`),
      ),
    [onDeck.data, recentlyAdded.data],
  )
  // The hero's slides come from the server already carrying their logo,
  // badge, certification, length and genres — see api.py's hero_slides.
  // Picking them here from the trending list instead would mean the
  // carousel fetching a detail per slide to learn any of that, which is
  // what used to keep the title logos a round trip behind the page.
  const hero = useQuery({ queryKey: ['hero', 'home'], queryFn: () => getHeroSlides('home') })
  const recommended = useRecommendedRows('home')

  // Sorted by most recently subscribed — see TvLandingPage's own note on
  // why the old app's richer "New In Watching" (sorted by most recently
  // *aired* episode) isn't ported: it needs a per-show TMDB enrichment
  // fetch that isn't worth adding twice over piecemeal.
  const subscribedShows = useMemo(
    () =>
      // Series still going only: a finished show drops off once its last
      // check records it as ended or cancelled.
      (shows.data ?? [])
        .filter((s) => s.status === 'watching' && s.tmdb_status !== 'Ended' && s.tmdb_status !== 'Canceled')
        .slice()
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .map((s) => ({ id: s.tmdb_id, name: s.title, poster_path: s.poster_path, mediaType: 'tv' as const })),
    [shows.data],
  )
  // Each section shows its own skeleton until its data arrives; the page
  // only gives way to an error once the trending lists and the rows have
  // all failed.
  const trendingLoading = movieTrending.isLoading || tvTrending.isLoading
  const firstError = movieTrending.error || tvTrending.error
  if (firstError && !movieTrending.data && !tvTrending.data && recommended.isError) {
    return <ErrorState message={firstError instanceof Error ? firstError.message : undefined} />
  }

  // The page's own rows — built from live household data the backend's
  // rows don't carry — keyed by the names its layout uses, so it decides
  // where the dealt ones sit around them. See useRecommendedRows.
  const ownRows: Record<string, ReactNode> = {
    top10: <TopTenRow key="top10" movies={movieTrending.data?.results ?? []} shows={tvTrending.data?.results ?? []} loading={trendingLoading} />,
    // Renders nothing when Plex has none, so pinning it costs nothing on a
    // household that hasn't started anything.
    continue: <ContinueWatchingRow key="continue" />,
    recent: <RecentlyAddedRow key="recent" />,
    requested: <RequestedRow key="requested" />,
  }
  if (shows.isLoading || subscribedShows.length) {
    ownRows.subscribed = <MediaRow key="subscribed" title="Shows" qualifier="you follow" items={subscribedShows} mediaType="tv" loading={shows.isLoading} renderSkeleton={followedSkeleton} expandHref="/tv/watching" />
  }
  const contentRows = recommended.order(ownRows, alreadyShown)

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
    </>
  )
}
