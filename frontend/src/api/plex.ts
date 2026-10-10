import { postJson, putJson, request } from './client'
import type { LinkedPlexServer, PlexStatus } from '../types/settings'
import type { PlexServerSummary } from '../types/auth'
import type { OnDeck, RecentlyAdded } from '../types/features'

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

// The servers Obsidian serves, and managing that list (admins). Each
// returns the whole list as it now stands.
export function listLinkedServers() {
  return request<LinkedPlexServer[]>('/api/admin/plex-servers')
}

export function addPlexServer(machineIdentifier: string) {
  return postJson<LinkedPlexServer[]>('/api/admin/plex-servers', { machine_identifier: machineIdentifier })
}

export function labelPlexServer(id: number, label: string) {
  return request<LinkedPlexServer[]>(`/api/admin/plex-servers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
  })
}

export function reorderPlexServers(serverIds: number[]) {
  return putJson<LinkedPlexServer[]>('/api/admin/plex-servers/order', { server_ids: serverIds })
}

export function removePlexServer(id: number) {
  return request<LinkedPlexServer[]>(`/api/admin/plex-servers/${id}`, { method: 'DELETE' })
}

// Plex's Continue Watching for the linked server (Home row).
export function getOnDeck() {
  return request<OnDeck>('/api/plex/on-deck')
}

export function getRecentlyAdded() {
  return request<RecentlyAdded>('/api/plex/recently-added')
}

export type PlexLocate = { available: false } | { available: true; rating_key: string; machine_id: string | null; title: string | null; year: number | null }

// Where a title lives on the linked server (detail page Play button).
export function locateOnPlex(type: 'movie' | 'show', title: string, year?: string | number | null, tmdbId?: number) {
  const q = new URLSearchParams({ type, title })
  if (year) q.set('year', String(year))
  if (tmdbId) q.set('tmdb_id', String(tmdbId))
  return request<PlexLocate>(`/api/plex/locate?${q.toString()}`)
}
