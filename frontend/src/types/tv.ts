import type { CastMember, CrewMember, Genre, LibrarySummary, ProductionCompany, TmdbListItem } from './movies'

export interface Network {
  id: number
  name: string
  logo_path: string | null
}

export interface SeasonSummary {
  id: number
  season_number: number
  name: string
  episode_count: number
  air_date: string | null
  poster_path: string | null
}

export interface ContentRatingEntry {
  iso_3166_1: string
  rating: string
}

export interface EpisodeToAir {
  air_date: string | null
  season_number?: number
  episode_number?: number
  name?: string
}

export interface TvDetail extends Omit<TmdbListItem, 'genre_ids'> {
  logo_path?: string | null
  status: string // "Returning Series" | "Ended" | "Canceled" | ...
  // "Scripted" | "Miniseries" | "Documentary" | … A limited series is
  // "Miniseries" and TMDB marks it "Ended" from the day it drops.
  type?: string
  number_of_episodes?: number
  last_air_date?: string | null
  original_language?: string
  genres: Genre[]
  networks: Network[]
  production_companies: ProductionCompany[]
  seasons: SeasonSummary[]
  credits?: { cast?: CastMember[]; crew?: CrewMember[] }
  content_ratings?: { results?: ContentRatingEntry[] }
  recommendations?: { results?: TmdbListItem[] }
  // Home hero's own "New episode <day>" / "All episodes available"
  // badge — TMDB includes both natively on the detail response.
  next_episode_to_air?: EpisodeToAir | null
  last_episode_to_air?: EpisodeToAir | null
  // Annotated server-side — see api.py's get_tv_detail.
  is_coming_soon: boolean
  // Part K3 — TV parity with the movie route. A show's organized history
  // is episode/pack rows, never a single fixed media_type the way a
  // movie's always is.
  on_plex_tracked: boolean
  // Every aired episode is already on Plex (by Plex's own episode count).
  plex_complete?: boolean
  // How many episodes of the show Plex holds — null when Plex isn't
  // linked or hasn't got the show. What the episode list counts, one per
  // episode, so a re-download isn't a second episode the way a file
  // tally makes it.
  plex_episode_count?: number | null
  // What this app has filed for the show across every season. Same
  // shape the movie detail carries — both pages render it as the same
  // "On disk" tiles.
  library?: LibrarySummary
}
