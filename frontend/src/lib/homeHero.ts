import { FALLBACK_REGION } from './regions'
import type { MovieDetail, ReleaseDatesResult, TmdbListItem } from '../types/movies'
import type { EpisodeToAir, TvDetail } from '../types/tv'

// The hero's wording is derived from these few fields and nothing more,
// so they are typed structurally rather than as whole detail objects: a
// /api/hero slide carries exactly this much and no more (see
// types/hero.ts), while a full MovieDetail/TvDetail still satisfies it,
// so the detail pages keep calling these unchanged. This is what lets
// the carousel stop fetching a detail per slide without any of the
// wording below being duplicated server-side.
type MovieBadgeSource = {
  is_coming_soon?: boolean
  release_date?: string | null
  release_dates?: { results?: ReleaseDatesResult[] }
}
type TvBadgeSource = {
  plex_complete?: boolean
  next_episode_to_air?: EpisodeToAir | null
  last_episode_to_air?: EpisodeToAir | null
}

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

// Midnight today, local time, for "has this aired yet" comparisons.
export function startOfToday(): Date {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d
}

// What the show is doing right now, for the status line. A weekly show
// says which day the next episode lands; one that dropped a whole
// season at once (TMDB's "next" episode shares the last one's air date,
// or has already aired) says every episode is out.
export function tvHeroBadge(show: TvBadgeSource): string | null {
  const badge = tvAiringBadge(show)
  // Every aired episode already on Plex: say so.
  return badge === 'All episodes available' && show.plex_complete ? 'All episodes available on Plex' : badge
}

function tvAiringBadge(show: TvBadgeSource): string | null {
  const next = show.next_episode_to_air
  const last = show.last_episode_to_air
  if (next?.air_date) {
    const nextDate = new Date(`${next.air_date}T00:00:00`)
    const today = startOfToday()
    if (nextDate > today) {
      const days = (nextDate.getTime() - today.getTime()) / 86400000
      if (days <= 7) return `New episode ${nextDate.toLocaleDateString(undefined, { weekday: 'long' })}`
      return `Next episode ${nextDate.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`
    }
    // Dated today with the last aired one, or already past (TMDB hasn't
    // moved on yet): the drop is out.
    if (nextDate < today || last?.air_date === next.air_date) return 'All episodes available'
    return 'New episode today'
  }
  if (last) return 'All episodes available'
  return null
}

// When a movie reached digital: the region's digital release, else any
// region's, else the theatrical date as a last resort.
export function digitalReleaseDate(movie: MovieBadgeSource, region: string = FALLBACK_REGION): string | null {
  const results = movie.release_dates?.results ?? []
  const pick = (r: typeof results[number]) => r.release_dates.find((d) => d.type === 4 && d.release_date)?.release_date?.slice(0, 10) ?? null
  const local = results.find((r) => r.iso_3166_1 === region)
  return (local && pick(local)) ?? results.map(pick).find(Boolean) ?? movie.release_date ?? null
}

function daysSince(date: string | null | undefined): number | null {
  if (!date) return null
  return (Date.now() - new Date(`${date}T00:00:00`).getTime()) / 86400000
}

export function movieHeroBadge(movie: MovieBadgeSource, region: string = FALLBACK_REGION): string | null {
  if (movie.is_coming_soon) return 'Coming soon'
  const age = daysSince(digitalReleaseDate(movie, region))
  if (age != null && age >= 0 && age <= 30) return 'Just dropped'
  return null
}

export interface BannerPill {
  text: string
  tone: 'plex' | 'hot' | 'soon'
}

// The pill under the logo on a content page: the one thing to know
// about this title right now.
export function moviePill(movie: MovieDetail): BannerPill | null {
  if (movie.on_plex) return { text: 'Watch now on Plex', tone: 'plex' }
  if (movie.is_coming_soon) return { text: 'Coming soon', tone: 'soon' }
  const age = daysSince(digitalReleaseDate(movie))
  if (age != null && age >= 0 && age <= 30) return { text: 'Just dropped', tone: 'hot' }
  return null
}

export function showPill(show: TvDetail): BannerPill | null {
  if (show.on_plex) return { text: 'Watch now on Plex', tone: 'plex' }
  if (show.is_coming_soon) return { text: 'Coming soon', tone: 'soon' }
  const started = daysSince(show.first_air_date)
  if (started != null && started >= 0 && started <= 30) return { text: 'Just dropped', tone: 'hot' }
  const last = show.last_episode_to_air
  const lastAge = daysSince(last?.air_date)
  if (last?.episode_number === 1 && lastAge != null && lastAge >= 0 && lastAge <= 30) return { text: 'New season', tone: 'hot' }
  return null
}

// The hero's certification lookups used to live here, reading a
// hardcoded 'AU' while detailHelpers read a hardcoded 'US' — the same
// title rated two ways on two pages of the same app. There is one
// lookup now, in detailHelpers, and it takes the household's region.
// Re-exported so the carousel's imports stay in one place.
export { certificationOf as movieCertOf, tvCertificationOf as tvCertOf } from './detailHelpers'
