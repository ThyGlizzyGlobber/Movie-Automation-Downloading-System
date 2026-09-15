import { postJson, putJson, request } from './client'
import type { NotificationPrefs, NotificationsOut } from '../types/features'

export function listNotifications() {
  return request<NotificationsOut>('/api/notifications')
}

export function markNotificationsRead(ids?: number[]) {
  return postJson<{ marked: number }>('/api/notifications/read', { ids: ids ?? null })
}

export function getNotificationPrefs() {
  return request<NotificationPrefs>('/api/notifications/preferences')
}

export function setNotificationPrefs(body: { notify_own: boolean; notify_household: boolean }) {
  return putJson<NotificationPrefs>('/api/notifications/preferences', body)
}

export function sendTestNotification() {
  return request<{ pushed: number }>('/api/notifications/test', { method: 'POST' })
}

export function getPushPublicKey() {
  return request<{ available: boolean; public_key: string | null }>('/api/push/public-key')
}

export function subscribePush(sub: PushSubscriptionJSON) {
  return postJson<{ devices: number }>('/api/push/subscribe', { endpoint: sub.endpoint, keys: sub.keys ?? {} })
}

export function unsubscribePush(endpoint: string) {
  return postJson<{ devices: number }>('/api/push/unsubscribe', { endpoint })
}
