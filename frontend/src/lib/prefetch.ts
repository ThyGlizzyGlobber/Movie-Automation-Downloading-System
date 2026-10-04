import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { getMovie } from '../api/movies'
import { getTvShow } from '../api/tv'

// Start a title's page loading the moment someone shows they mean to
// open it — a finger landing on the poster, the pointer settling on it,
// keyboard focus — rather than when the click lands. A touch is
// ~100-300ms ahead of the click it becomes, and a hover usually longer,
// which is most of the detail call's own time. Same keys and fetchers
// as MovieDetailPage and ShowDetailPage, so the page finds it answered;
// anything already fresh in the cache is left alone.
export function prefetchTitle(queryClient: QueryClient, mediaType: 'movie' | 'tv', tmdbId: number) {
  if (mediaType === 'tv') void queryClient.prefetchQuery({ queryKey: ['tv', tmdbId], queryFn: () => getTvShow(tmdbId) })
  else void queryClient.prefetchQuery({ queryKey: ['movie', tmdbId], queryFn: () => getMovie(tmdbId) })
}

// The handlers to spread onto a link to that title.
export function prefetchHandlers(queryClient: QueryClient, mediaType: 'movie' | 'tv', tmdbId: number | null | undefined) {
  if (!tmdbId) return {}
  const prefetch = () => prefetchTitle(queryClient, mediaType, tmdbId)
  return { onPointerEnter: prefetch, onTouchStart: prefetch, onFocus: prefetch }
}

export function usePrefetchTitle(mediaType: 'movie' | 'tv', tmdbId: number | null | undefined) {
  return prefetchHandlers(useQueryClient(), mediaType, tmdbId)
}
