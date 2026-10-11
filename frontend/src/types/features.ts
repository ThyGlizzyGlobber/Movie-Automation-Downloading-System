// Shapes for the Obsidian feature pass (api.py's "Obsidian feature
// pass" block): quality profiles, per-episode status, storage details,
// Plex Continue Watching.

// 'holding': aired, but still inside the air buffer (Settings › TV
// scheduling), so the scheduler is deliberately waiting before it
// searches — distinct from 'unaired', which hasn't come out at all.
export type EpisodeState = 'in_plex' | 'requested' | 'failed' | 'unaired' | 'holding' | 'missing'

export interface EpisodeStatus {
  episode_number: number
  name: string | null
  overview: string | null
  air_date: string | null
  /** The exact release moment (UTC ISO), when TVmaze knows it. */
  airs_at?: string | null
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

export interface HouseholdUser {
  plex_user_id: string
  username: string | null
  is_admin: boolean
  avatar?: boolean
  can_request: boolean
  first_seen_at: string
  last_login_at: string
  requests: number
  // The Plex servers they could reach at their last sign-in, primary first.
  server_ids?: number[]
}
export interface LibrarySettings {
  movie_library_root: string
  tv_library_root: string
  source: 'env' | 'db'
  plex_refresh_after_import: boolean
  free_space_floor_gb: number
}
// A box's drives (backend/app/api/storage.py). Off a box: { managed: false }.
export interface BoxDrive {
  id: string
  label: string
  size: number | null
  used: number | null
  free: number | null
  // missing: the NAS has its pool but it isn't reachable (failed or pulled out).
  state: 'healthy' | 'problem' | 'missing'
  status: string | null
}
export interface FreeDisk {
  name: string
  serial: string
  model: string | null
  size: number
  type: string
  // Holds a pool from another system: adding it erases that too.
  has_other_data: boolean
}
export type DrivesInfo =
  | { managed: false }
  | {
      managed: true
      drives: BoxDrive[]
      unused: FreeDisk[]
      report_fresh: boolean
      report_error: string | null
      pending: boolean
      last: { state: 'adding' | 'added' | 'failed'; drive: string | null; message: string; at: string } | null
      updater_running: boolean
    }

// The box's VPN (backend/app/vpn.py). Never carries a secret.
export type VpnField = 'private_key' | 'public_key' | 'preshared_key' | 'addresses' | 'endpoint_ip' | 'endpoint_port' | 'user' | 'password'
export interface VpnProvider {
  id: string
  label: string
  types: { wireguard?: VpnField[]; openvpn?: VpnField[] }
}
export interface VpnStatus {
  reachable: boolean
  connected: boolean
  public_ip: string | null
  country: string | null
}
export interface VpnApplied {
  // A change saved and waiting for the box's updater (at most a minute).
  pending: boolean
  state: 'applying' | 'applied' | 'failed' | null
  provider: string | null
  type: 'wireguard' | 'openvpn' | null
  server_countries: string | null
  message: string | null
  at: string | null
}
export type VpnOverview =
  | { managed: false }
  | { managed: true; providers: VpnProvider[]; applied: VpnApplied; status: VpnStatus; updater_running: boolean }
export interface VpnInput {
  provider: string
  type?: 'wireguard' | 'openvpn'
  server_countries?: string
  wireguard_file?: string
  [field: string]: string | undefined
}

// The updater's state (truenas/obsidian-updater.py, on the NAS), as
// GET/POST /api/admin/updates report it.
export interface UpdaterStatus {
  // Both say whether the updater has run on this box in the last few minutes.
  reachable: boolean
  configured: boolean
  // queued: asked for, waiting for the updater's next run (at most a minute).
  state: 'idle' | 'queued' | 'updating' | 'verifying' | 'rolling_back' | 'done' | 'rolled_back' | 'failed'
  from_version: string | null
  to_version: string | null
  message: string | null
  started_at: string | null
  finished_at: string | null
}
export interface ReleaseNoteGroup {
  heading: string
  items: string[]
}
export type UpdatesInfo =
  | { method: 'git'; current: string | null; channel: null }
  | {
      method: 'image'
      current: string | null
      channel: string
      latest: string | null
      available: boolean
      // The newest release, when it's the one that was just rolled back:
      // not offered again until a newer one is published.
      skipped: string | null
      notes: ReleaseNoteGroup[]
      error: string | null
      updater: UpdaterStatus
    }
export interface AboutInfo {
  name: string
  // "1.0.3": the release tag plus one per commit since (backend/app/version.py).
  version: string | null
  commit: string | null
  date: string | null
  // "stable" | "beta" on a release image, which updates by image rather than
  // git pull; null on a git-pull install.
  channel: string | null
  python: string
  started_at: string | null
  uptime_seconds: number | null
  plex_server_name: string | null
  requests: number
  users: number
  db_bytes: number | null
  movie_library_root: string
  tv_library_root: string
}
