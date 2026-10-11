import { postJson, putJson, request } from './client'
import type { AboutInfo, HouseholdUser, UpdatesInfo, UpdaterStatus } from '../types/features'

export function postDeploy() {
  return request<{ detail: string; commit: string }>('/api/admin/deploy', { method: 'POST' })
}

// A release install's updates (backend/app/api/updates.py). `refresh` asks
// the registry again rather than reusing the last answer.
export function getUpdates(refresh = false) {
  return request<UpdatesInfo>(`/api/admin/updates${refresh ? '?refresh=true' : ''}`)
}

export function installUpdate(version: string) {
  return postJson<UpdaterStatus>('/api/admin/updates', { version })
}

export function listHousehold() {
  return request<HouseholdUser[]>('/api/admin/users')
}

export function setHouseholdUser(plexUserId: string, canRequest: boolean) {
  return putJson<{ can_request: boolean }>(`/api/admin/users/${encodeURIComponent(plexUserId)}`, { can_request: canRequest })
}

export function removeHouseholdUser(plexUserId: string) {
  return request<{ removed: true }>(`/api/admin/users/${encodeURIComponent(plexUserId)}`, { method: 'DELETE' })
}

export function getAbout() {
  return request<AboutInfo>('/api/about')
}
