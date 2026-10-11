import { postJson, request } from './client'
import type { DrivesInfo } from '../types/features'

// Settings › Storage on a box (backend/app/api/storage.py). Adding erases the
// disk, so it's named by name and serial, and confirmed.
export function getDrives() {
  return request<DrivesInfo>('/api/admin/drives')
}

export function addDrive(body: { disk: string; serial: string; confirm: true; erase_other_data: boolean }) {
  return postJson<{ pending: true }>('/api/admin/drives', body)
}
