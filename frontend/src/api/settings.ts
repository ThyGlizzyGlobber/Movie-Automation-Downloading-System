import { postJson, putJson, request } from './client'
import type {
  ActivityOut,
  AuditLogOut,
  PipelineSettings,
  PipelineSettingsBody,
  QbtSettingsResult,
  RemoteAccessSettings,
  RetentionSettings,
  TmdbSettingsResult,
  TvScheduleSettings,
  TvScheduleSettingsBody,
} from '../types/settings'
import type { QbtConnectionInput } from './setup'
import type { QualityProfiles, StorageDetails } from '../types/features'

export function getRetention() {
  return request<RetentionSettings>('/api/settings/retention')
}

export function setRetention(days: number | null) {
  return putJson<RetentionSettings>('/api/settings/retention', { days })
}

export function getPipelineSettings() {
  return request<PipelineSettings>('/api/settings/pipeline')
}

export function setPipelineSettings(body: PipelineSettingsBody) {
  return putJson<PipelineSettings>('/api/settings/pipeline', body)
}

export function getTvSettings() {
  return request<TvScheduleSettings>('/api/settings/tv')
}

export function setTvSettings(body: TvScheduleSettingsBody) {
  return putJson<TvScheduleSettings>('/api/settings/tv', body)
}

// Connections panel (Part I) — the ongoing, always-admin-gated
// equivalent of the one-time setup wizard's TMDB/qBittorrent steps; the
// /api/setup/* routes 410 permanently once setup completes.
export function updateTmdbSettings(apiKey: string) {
  return putJson<TmdbSettingsResult>('/api/settings/tmdb', { api_key: apiKey })
}

export function testQbtSettingsConnection(body: QbtConnectionInput) {
  return postJson<{ reachable: boolean; detail?: string }>('/api/settings/qbittorrent/test', body)
}

export function updateQbtSettings(body: QbtConnectionInput) {
  return putJson<QbtSettingsResult>('/api/settings/qbittorrent', body)
}

// Activity Dashboard (Part D).
export function getActivity(limit = 50, offset = 0) {
  return request<ActivityOut>(`/api/admin/activity?limit=${limit}&offset=${offset}`)
}

// Remote Access panel (Part G2) + the audit log/"revoke all sessions"
// panic button (Part G4).
export function getRemoteAccess() {
  return request<RemoteAccessSettings>('/api/settings/remote-access')
}

export function setRemoteAccess(body: RemoteAccessSettings) {
  return putJson<RemoteAccessSettings>('/api/settings/remote-access', body)
}

export function getAuditLog(limit = 25, offset = 0) {
  return request<AuditLogOut>(`/api/admin/audit-log?limit=${limit}&offset=${offset}`)
}

// Quality profiles (Settings › Quality profiles) and the Storage panel.
export function getQualityProfilesAdmin() {
  return request<QualityProfiles>('/api/settings/quality-profiles')
}

export function setQualityProfiles(body: QualityProfiles) {
  return putJson<QualityProfiles>('/api/settings/quality-profiles', body)
}

export function getStorageDetails() {
  return request<StorageDetails>('/api/storage/details')
}

export function revokeAllSessions() {
  return postJson<{ revoked: number }>('/api/admin/revoke-sessions', {})
}
