import { putJson, request } from './client'
import type { PlexStatus } from '../types/settings'
import type { PlexServerSummary } from '../types/auth'

const SETUP_TOKEN_HEADER = 'X-Setup-Token'

// `setupToken` is only needed for the bootstrap window (no server linked
// yet — see api.py's require_admin_or_setup_bootstrap); once linked,
// these routes are admin-session-gated instead and the param is unused.

export function startPlexLink(setupToken?: string) {
  return request<{ auth_url: string }>('/api/plex/link', {
    method: 'POST',
    headers: setupToken ? { [SETUP_TOKEN_HEADER]: setupToken } : undefined,
  })
}

export function getPlexStatus(setupToken?: string) {
  return request<PlexStatus>('/api/plex/status', {
    headers: setupToken ? { [SETUP_TOKEN_HEADER]: setupToken } : undefined,
  })
}

export function unlinkPlex() {
  return request<PlexStatus>('/api/plex/unlink', { method: 'POST' })
}

export function listPlexServers(setupToken?: string) {
  return request<PlexServerSummary[]>('/api/plex/servers', {
    headers: setupToken ? { [SETUP_TOKEN_HEADER]: setupToken } : undefined,
  })
}

export function selectPlexServer(machineIdentifier: string, setupToken?: string) {
  return putJson<{ server_name: string }>(
    '/api/plex/server',
    { machine_identifier: machineIdentifier },
    setupToken ? { [SETUP_TOKEN_HEADER]: setupToken } : undefined,
  )
}
