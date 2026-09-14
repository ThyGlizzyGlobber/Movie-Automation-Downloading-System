import { request } from './client'

export interface HealthStatus {
  status: 'ok'
  qbittorrent: boolean
}

// Both variants api.py's get_storage() (api.py:975-1008) can return —
// discriminate on `available`.
export type StorageStatus =
  | { available: false }
  | {
      available: true
      total_bytes: number
      used_bytes: number
      free_bytes: number
      used_percent: number
    }

export function getHealth() {
  return request<HealthStatus>('/api/health')
}

export function getStorage() {
  return request<StorageStatus>('/api/storage')
}
