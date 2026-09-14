// Mirrors api.py's /api/auth/* and /api/setup/* response shapes.

export interface SessionInfo {
  username: string | null
  is_admin: boolean
  has_seen_tutorial: boolean
}

export interface LoginStartResponse {
  auth_url: string
}

export interface LoginStatusResponse {
  pending: boolean
  authenticated: boolean
  username: string | null
  is_admin: boolean | null
  has_seen_tutorial: boolean | null
  error: string | null
}

export interface SetupStatus {
  tmdb_configured: boolean
  tmdb_source: 'env' | 'db' | null
  qbt_configured: boolean
  qbt_source: 'env' | 'db'
  // `plex_account_linked` — a Plex account has signed in (PIN flow done),
  // but not necessarily picked a server yet. `plex_linked`/`setup_complete`
  // both mean the server has actually been *selected* — see api.py's
  // require_admin_or_setup_bootstrap docstring for why these are distinct.
  plex_account_linked: boolean
  plex_linked: boolean
  setup_complete: boolean
}

export interface SetupMutationResult {
  restart_required: true
}

export interface PlexServerSummary {
  name: string
  machine_identifier: string
}
