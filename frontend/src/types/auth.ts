// Mirrors api.py's /api/auth/* and /api/setup/* response shapes.

export interface SessionInfo {
  username: string | null
  is_admin: boolean
  has_seen_tutorial: boolean
  // Whether /api/me/avatar has a picture (the Plex account's avatar).
  avatar?: boolean
  // Household-wide: which country's age ratings the UI shows.
  certification_region?: string
  // Whether the Requests page offers "Clear finished" (Settings › History
  // can keep it to admins).
  can_clear_requests?: boolean
  // The Plex server this session is served by, and — for an admin, who
  // can switch between them — every linked server. Empty for anyone else.
  server?: SessionServer | null
  servers?: SessionServer[]
}

export interface SessionServer {
  id: number
  name: string
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
  // A box has its own VPN, set up in the wizard (backend/app/box/vpn.py).
  vpn_managed: boolean
  vpn_configured: boolean
  // A box has its own Plex, claimed for the buyer as they sign in.
  plex_on_box: boolean
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
  restart_required: boolean
}

export interface PlexServerSummary {
  name: string
  machine_identifier: string
  // Already one of the servers this app serves.
  linked?: boolean
}
