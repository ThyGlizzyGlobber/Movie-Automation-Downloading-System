import type { MovieDetail, TmdbListItem } from '../types/movies'
import type { TvDetail } from '../types/tv'

// Same region as the old app's own hero cert pill.
const HERO_CERT_REGION = 'AU'

export interface TaggedItem extends TmdbListItem {
  mediaType: 'movie' | 'tv'
}

export function tagMediaType(items: TmdbListItem[], mediaType: 'movie' | 'tv'): TaggedItem[] {
  return items.map((it) => ({ ...it, mediaType }))
}

// Trending movies+TV interleaved into one popularity-sorted list — backs
// both the "Trending Now" row and (its top few, backdrop-having entries)
// the hero carousel's own slide picks.
export function mixTrending(movies: TmdbListItem[], shows: TmdbListItem[], limit = 20): TaggedItem[] {
  return [...tagMediaType(movies, 'movie'), ...tagMediaType(shows, 'tv')]
    .sort((a, b) => (b.popularity || 0) - (a.popularity || 0))
    .slice(0, limit)
}

export function tvHeroBadge(show: TvDetail): string | null {
  const next = show.next_episode_to_air
  if (next?.air_date) {
    const day = new Date(`${next.air_date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long' })
    return `New episode ${day}`
  }
  if (show.last_episode_to_air) return 'All episodes available'
  return null
}

export function movieHeroBadge(movie: MovieDetail): string | null {
  if (movie.is_coming_soon) return 'Coming Soon'
  if (movie.release_date) {
    const ageDays = (Date.now() - new Date(`${movie.release_date}T00:00:00`).getTime()) / 86400000
    if (ageDays >= 0 && ageDays <= 30) return 'Newly Released'
  }
  return null
}

export function tvCertOf(show: TvDetail): string {
  return show.content_ratings?.results?.find((r) => r.iso_3166_1 === HERO_CERT_REGION)?.rating ?? ''
}

export function movieCertOf(movie: MovieDetail): string {
  const entry = movie.release_dates?.results?.find((r) => r.iso_3166_1 === HERO_CERT_REGION)
  const withCert = entry?.release_dates.find((rd) => rd.certification)
  return withCert?.certification ?? ''
}
