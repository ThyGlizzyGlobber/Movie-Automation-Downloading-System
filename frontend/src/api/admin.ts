import { request } from './client'
import type { RequestOut } from '../types/requests'

export function getAdminJobs(status?: string) {
  return request<RequestOut[]>(`/api/admin/jobs${status ? `?status=${status}` : ''}`)
}

export function postDeploy() {
  return request<{ detail: string; commit: string }>('/api/admin/deploy', { method: 'POST' })
}
