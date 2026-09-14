import { postJson, request } from './client'
import type { CreateRequestBody, RequestOut } from '../types/requests'

export function listRequests(status?: string) {
  return request<RequestOut[]>(`/api/requests${status ? `?status=${status}` : ''}`)
}

export function createRequest(body: CreateRequestBody) {
  return postJson<RequestOut>('/api/requests', body)
}

export function getRequest(id: number) {
  return request<RequestOut>(`/api/requests/${id}`)
}

export function cancelRequest(id: number) {
  return request<RequestOut>(`/api/requests/${id}/cancel`, { method: 'POST' })
}

export function rejectRequest(id: number) {
  return request<RequestOut>(`/api/requests/${id}/reject`, { method: 'POST' })
}

export function clearRequests() {
  return request<{ removed: number }>('/api/requests/clear', { method: 'POST' })
}
