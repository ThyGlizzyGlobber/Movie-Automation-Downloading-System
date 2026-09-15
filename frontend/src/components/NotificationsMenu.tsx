import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { listNotifications, markNotificationsRead } from '../api/notifications'
import { relativeTime } from '../lib/format'
import Icon from './Icon'
import './NotificationsMenu.css'

// The bell: unread dot, and a glass panel listing what landed or failed.
// While a tab is open, new items also fire the browser's own
// notification when that permission was granted.
export default function NotificationsMenu() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['notifications'], queryFn: listNotifications, refetchInterval: 15_000 })
  const items = query.data?.items ?? []
  const unread = query.data?.unread ?? 0
  const seenRef = useRef<number | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!query.data) return
    const newest = items[0]?.id ?? 0
    if (seenRef.current !== null && newest > seenRef.current && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      for (const it of items) {
        if (it.id > seenRef.current && !it.read_at) {
          try {
            new Notification(it.title, { body: it.body ?? undefined, icon: '/icon-512.png', tag: `meridian-${it.id}` })
          } catch {
            // notifications can be blocked at the OS level; the bell still shows it
          }
        }
      }
    }
    seenRef.current = newest
  }, [query.data, items])

  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  async function toggle() {
    const next = !open
    setOpen(next)
    if (next && unread > 0) {
      await markNotificationsRead()
      queryClient.invalidateQueries({ queryKey: ['notifications'] })
    }
  }

  return (
    <div className="notif" ref={ref}>
      <button id="bellToggle" aria-label={unread ? `${unread} new notifications` : 'Notifications'} aria-expanded={open} onClick={toggle}>
        <Icon name="bell" />
        {unread > 0 && <span className="bell-dot" />}
      </button>
      {open && (
        <div className="notif-panel" role="dialog" aria-label="Notifications">
          <div className="notif-head">
            <b>Notifications</b>
            <button
              className="notif-link"
              onClick={() => {
                setOpen(false)
                navigate('/settings#settings-notifications')
              }}
            >
              Settings
            </button>
          </div>
          {items.length === 0 ? (
            <div className="notif-empty">Nothing yet. You'll hear when a request lands.</div>
          ) : (
            <div className="notif-list">
              {items.map((it) => (
                <button
                  key={it.id}
                  className={`notif-item ${it.kind}${it.read_at ? '' : ' unread'}`}
                  onClick={() => {
                    setOpen(false)
                    navigate('/requests')
                  }}
                >
                  <span className="notif-circ">
                    <Icon name={it.kind === 'landed' ? 'check-circle' : it.kind === 'failed' ? 'alert' : 'bell'} />
                  </span>
                  <span className="notif-text">
                    <b>{it.title}</b>
                    {it.body && <small>{it.body}</small>}
                  </span>
                  <span className="notif-time">{relativeTime(it.created_at)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
