// Movie/TV detail and discover endpoints are typed `-> dict` in api.py
// (raw TMDB passthrough plus a couple of annotated fields), not a Pydantic
// response model — see A5 in the migration plan for why this is
// hand-written rather than codegen'd. These interfaces cover the fields
// this app's UI actually reads; TMDB's real response has more. Extend as
// new fields are actually needed rather than trying to model the whole
// TMDB schema up front.

export interface TmdbListItem {
  id: number
  title?: string // movies
  name?: string // tv
  original_title?: string
  original_name?: string
  overview?: string
  poster_path: string | null
  backdrop_path: string | null
  release_date?: string // movies
  first_air_date?: string // tv
  vote_average?: number
  genre_ids?: number[]
  // Annotated server-side — see api.py's _annotate_on_plex.
  on_plex: boolean
}

export interface TmdbListResponse {
  page: number
  total_pages: number
  total_results: number
  results: TmdbListItem[]
}

export interface Genre {
  id: number
  name: string
}

export interface ProductionCompany {
  id: number
  name: string
  logo_path: string | null
}

export interface WatchProviderEntry {
  provider_id: number
  provider_name: string
  logo_path: string
}

export interface MovieDetail extends Omit<TmdbListItem, 'genre_ids'> {
  runtime: number | null
  genres: Genre[]
  production_companies: ProductionCompany[]
  'watch/providers'?: { results?: { US?: { flatrate?: WatchProviderEntry[] } } }
  credits?: { cast?: CastMember[] }
  // Annotated server-side — see api.py's get_movie_detail.
  is_coming_soon: boolean
}

export interface CastMember {
  id: number
  name: string
  character?: string
  profile_path: string | null
}

export interface MovieTrailer {
  url: string | null
}

export interface PersonCredit {
  id: number
  media_type: 'movie' | 'tv'
  title?: string
  name?: string
  poster_path: string | null
  release_date?: string
  first_air_date?: string
}

// GET /api/person/{id} — api.py:537-568, a deliberately narrowed shape,
// not raw TMDB passthrough.
export interface PersonDetail {
  id: number
  name: string
  profile_path: string | null
  known_for_department: string | null
  credits: PersonCredit[]
}
