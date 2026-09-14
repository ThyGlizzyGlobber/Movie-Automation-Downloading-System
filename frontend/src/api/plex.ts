import { request } from './client'
import type { PlexStatus } from '../types/settings'

export function startPlexLink() {
  return request<{ auth_url: string }>('/api/plex/link', { method: 'POST' })
}

export function getPlexStatus() {
  return request<PlexStatus>('/api/plex/status')
}

export function unlinkPlex() {
  return request<PlexStatus>('/api/plex/unlink', { method: 'POST' })
}
