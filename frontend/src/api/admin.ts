import { putJson, request } from './client'
import type { RequestOut } from '../types/requests'
import type { AboutInfo, HouseholdUser } from '../types/features'

export function getAdminJobs(status?: string) {
  return request<RequestOut[]>(`/api/admin/jobs${status ? `?status=${status}` : ''}`)
}

export function postDeploy() {
  return request<{ detail: string; commit: string }>('/api/admin/deploy', { method: 'POST' })
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
