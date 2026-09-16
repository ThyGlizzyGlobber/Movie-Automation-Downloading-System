// Shapes for the Obsidian feature pass (api.py's "Obsidian feature
// pass" block): quality profiles, per-episode status, storage details,
// Plex Continue Watching.

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

export interface RecentlyAddedItem {
  rating_key: string
  media_type: 'movie' | 'tv'
  title: string | null
  year: number | null
  added_at: number | null
  tmdb_id: number | null
  poster_url: string | null
}
export type RecentlyAdded = { available: false; items: [] } | { available: true; items: RecentlyAddedItem[] }

export interface NotificationItem {
  id: number
  request_id: number | null
  kind: 'landed' | 'failed' | 'test' | string
  title: string
  body: string | null
  created_at: string
  read_at: string | null
}
export interface NotificationsOut {
  items: NotificationItem[]
  unread: number
}
export interface NotificationPrefs {
  notify_own: boolean
  notify_household: boolean
  push_available: boolean
  devices: number
}
export interface HouseholdUser {
  plex_user_id: string
  username: string | null
  is_admin: boolean
  avatar?: boolean
  can_request: boolean
  first_seen_at: string
  last_login_at: string
  requests: number
}
export interface LibrarySettings {
  movie_library_root: string
  tv_library_root: string
  source: 'env' | 'db'
  plex_refresh_after_import: boolean
  free_space_floor_gb: number
}
export interface AboutInfo {
  name: string
  version: string | null
  python: string
  started_at: string | null
  uptime_seconds: number | null
  plex_server_name: string | null
  requests: number
  users: number
  db_bytes: number | null
  movie_library_root: string
  tv_library_root: string
  push_available: boolean
}
