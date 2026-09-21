import type { Genre, ReleaseDatesResult, TmdbListItem } from './movies'
import type { ContentRatingEntry, EpisodeToAir } from './tv'

// One landing-page hero slide, as /api/hero returns it: a trending list
// item with the handful of detail-only fields a hero shows folded in.
//
// Those fields used to be fetched by the carousel itself, one detail
// call per slide, which meant the title logo couldn't even begin
// loading until two round trips had finished in sequence. The server
// does it now, in parallel and cached — see api.py's hero_slides.
//
// Everything here stays raw. The wording built out of it — the badge,
// the certification, "2 seasons", the genre line — is still derived in
// lib/homeHero.ts, so there is exactly one place that decides what a
// hero says, rather than one in TypeScript and a second in Python.
export interface HeroSlide extends TmdbListItem {
  mediaType: 'movie' | 'tv'
  logo_path?: string | null
  genres?: Genre[]
  is_coming_soon?: boolean

  // Movies.
  runtime?: number | null
  release_dates?: { results?: ReleaseDatesResult[] }

  // Shows. `seasons` is trimmed to the numbers server-side — the count
  // is all the length line needs.
  seasons?: { season_number: number }[]
  content_ratings?: { results?: ContentRatingEntry[] }
  next_episode_to_air?: EpisodeToAir | null
  last_episode_to_air?: EpisodeToAir | null
  plex_complete?: boolean
}
