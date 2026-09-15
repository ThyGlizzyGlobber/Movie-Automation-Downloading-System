import { getPushPublicKey, subscribePush, unsubscribePush } from '../api/notifications'

// Web Push on this device: the service worker (public/sw.js) shows the
// notification; the subscription is stored server-side per user.

export function pushSupported(): boolean {
  return typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4)
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null
  try {
    return await navigator.serviceWorker.register('/sw.js')
  } catch {
    return null
  }
}

export async function currentPushSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null
  const reg = await navigator.serviceWorker.getRegistration()
  return (await reg?.pushManager.getSubscription()) ?? null
}

export async function enablePushOnThisDevice(): Promise<'on' | 'denied' | 'unavailable'> {
  if (!pushSupported()) return 'unavailable'
  const key = await getPushPublicKey()
  if (!key.available || !key.public_key) return 'unavailable'
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return 'denied'
  const reg = (await navigator.serviceWorker.getRegistration()) ?? (await registerServiceWorker())
  if (!reg) return 'unavailable'
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key.public_key) }))
  await subscribePush(sub.toJSON())
  return 'on'
}

export async function disablePushOnThisDevice(): Promise<void> {
  const sub = await currentPushSubscription()
  if (!sub) return
  await unsubscribePush(sub.endpoint)
  await sub.unsubscribe()
}
