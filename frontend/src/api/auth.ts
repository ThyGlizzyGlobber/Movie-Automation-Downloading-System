import { request } from './client'
import type { LoginStartResponse, LoginStatusResponse, SessionInfo } from '../types/auth'

export function startLogin() {
  return request<LoginStartResponse>('/api/auth/login/start', { method: 'POST' })
}

export function getLoginStatus() {
  return request<LoginStatusResponse>('/api/auth/login/status')
}

export function getSession() {
  return request<SessionInfo>('/api/auth/session')
}

export function logout() {
  return request<{ logged_out: true }>('/api/auth/logout', { method: 'POST' })
}

export function markTutorialSeen() {
  return request<{ has_seen_tutorial: true }>('/api/auth/tutorial-seen', { method: 'PUT' })
}
