import type { Genre, ProductionCompany, TmdbListItem } from './movies'

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

export interface TvDetail extends Omit<TmdbListItem, 'genre_ids'> {
  status: string // "Returning Series" | "Ended" | "Canceled" | ...
  genres: Genre[]
  networks: Network[]
  production_companies: ProductionCompany[]
  seasons: SeasonSummary[]
  credits?: { cast?: import('./movies').CastMember[] }
  // Annotated server-side — see api.py's get_tv_detail.
  is_coming_soon: boolean
}
