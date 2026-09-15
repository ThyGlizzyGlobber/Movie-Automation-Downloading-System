// Shapes for the Obsidian feature pass (api.py's "Obsidian feature
// pass" block): quality profiles, per-episode status, storage details,
// Plex Continue Watching.

export interface QualityProfile {
  id: string
  name: string
  description: string | null
  min_resolution: string | null
  typical_size_gb: number | null
}

export interface QualityProfiles {
  profiles: QualityProfile[]
  default_profile_id: string
}

export type EpisodeState = 'in_plex' | 'requested' | 'failed' | 'unaired' | 'missing'

export interface EpisodeStatus {
  episode_number: number
  name: string | null
  overview: string | null
  air_date: string | null
  runtime: number | null
  still_path: string | null
  state: EpisodeState
  status: string | null
  download_progress: number | null
  request_id: number | null
}

export interface SeasonEpisodes {
  season_number: number
  episodes: EpisodeStatus[]
  in_plex: number
  aired: number
}

export interface LibraryUsage {
  key: 'movies' | 'tv'
  label: string
  root: string
  bytes: number | null
}

export type StorageDetails = (
  | { available: false }
  | { available: true; total_bytes: number; used_bytes: number; free_bytes: number; used_percent: number }
) & {
  libraries: LibraryUsage[]
  downloading: number
  queued: number
  completed_today: number
  completed_week: number
}

export interface OnDeckItem {
  rating_key: string
  type: 'movie' | 'episode'
  media_type: 'movie' | 'tv'
  title: string | null
  show_title: string | null
  season_number: number | null
  episode_number: number | null
  year: number | null
  progress: number
  remaining_minutes: number | null
  tmdb_id: number | null
  art_url: string | null
}

export type OnDeck = { available: false; items: [] } | { available: true; machine_id: string | null; items: OnDeckItem[] }
