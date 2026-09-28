import { useQuery } from '@tanstack/react-query'
import DiscoverLanding, { type DiscoverSource } from '../discover/DiscoverLanding'
import { getTvDiscoverTrending, listShows } from '../../api/tv'
import MediaRow from '../../components/media/MediaRow'
import { PosterCardSkeleton } from '../../components/ui/Skeleton'

// Same curated, row-based page as MoviesLandingPage, TV-only.
//
// The old app's "New In Watching" row (subscribed shows sorted by most
// recently *aired* episode) is a deliberate, documented gap here — it
// needs an extra per-show TMDB enrichment fetch (last_episode_to_air)
// that only paid for itself on Home/TV pages together. "Subscribed
// Shows" (sorted by most recently *subscribed*, below) needs no such
// fetch — ShowOut already carries poster_path — so it's the one ported.
// Its genre and service rows are defined with the rest of the page's rows
// in backend/app/api/recommendations.py (TV_GENRES, ROW_PROVIDERS).
const TV: DiscoverSource = {
  page: 'tv',
  mediaType: 'tv',
  title: 'TV Shows',
  trending: getTvDiscoverTrending,
}

// Followed shows carry no year or genre, so their cards have no meta line.
const followedSkeleton = () => <PosterCardSkeleton meta={false} />

export default function TvLandingPage() {
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => listShows() })

  // Most-recently-subscribed first — not blocking/critical, so a failed
  // fetch here just means an empty (hidden) row rather than an error page.
  // Series still going only: a finished show drops off once its last
  // check records it as ended or cancelled.
  const subscribedShows = (shows.data ?? [])
    .filter((s) => s.status === 'watching' && s.tmdb_status !== 'Ended' && s.tmdb_status !== 'Canceled')
    .slice()
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .map((s) => ({ id: s.tmdb_id, name: s.title, poster_path: s.poster_path }))

  // On this page the pins are Top 10 then Shows you follow: on a page
  // about television, the shows you are already mid-way through outrank
  // anything discovery has to offer.
  return (
    <DiscoverLanding
      source={TV}
      extraRows={{
        subscribed: <MediaRow key="subscribed" title="Shows" qualifier="you follow" items={subscribedShows} mediaType="tv" loading={shows.isLoading} renderSkeleton={followedSkeleton} expandHref="/tv/watching" />,
      }}
    />
  )
}
