import { postJson, request } from './client'
import type { DrivesInfo, LostOnDrive } from '../types/features'

// Settings › Storage on a box (backend/app/api/storage.py). Adding erases the
// disk, so it's named by name and serial, and confirmed.
export function getDrives() {
  return request<DrivesInfo>('/api/admin/drives')
}

export function addDrive(body: { disk: string; serial: string; confirm: true; erase_other_data: boolean }) {
  return postJson<{ pending: true }>('/api/admin/drives', body)
}

// A drive the box can't find: what was on it, and asking for it all again.
export function getLost(driveId: string) {
  return request<LostOnDrive>(`/api/admin/drives/${encodeURIComponent(driveId)}/lost`)
}

export function downloadLostAgain(driveId: string) {
  return postJson<{ requested: number; joined: number; failed: string[] }>(
    `/api/admin/drives/${encodeURIComponent(driveId)}/lost`,
    {},
  )
}
