import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getStorage } from '../api/system'
import { getNotificationPrefs } from '../api/notifications'
import Toggle from './Toggle'
import { formatBytes } from '../lib/format'
import { posterUrl } from '../lib/tmdbImage'
import Icon from './Icon'
import './RequestModal.css'

// The request sheet: what's being asked for, what the household will get
// (always the best copy that exists — no quality to pick), how much room
// the library has, and one button.
export default function RequestModal({
  open,
  title,
  subtitle,
  posterPath,
  submitLabel = 'Add to Plex',
  onClose,
  onSubmit,
}: {
  open: boolean
  title: string
  subtitle?: string
  posterPath?: string | null
  submitLabel?: string
  onClose: () => void
  onSubmit: (notify: boolean) => void
}) {
  const storage = useQuery({ queryKey: ['storage'], queryFn: getStorage, enabled: open, staleTime: 60_000 })
  const prefs = useQuery({ queryKey: ['notification-prefs'], queryFn: getNotificationPrefs, enabled: open, staleTime: 60_000 })
  const [notify, setNotify] = useState<boolean | null>(null)
  const notifyOn = notify ?? prefs.data?.notify_own ?? true

  useEffect(() => {
    if (open) setNotify(null)
  }, [open])
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  const free = storage.data?.available ? storage.data.free_bytes : null

  return (
    <div
      className="request-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="request-modal" role="dialog" aria-modal="true" aria-labelledby="request-modal-title">
        <button className="request-modal-close" aria-label="Close" onClick={onClose}>
          <Icon name="close" />
        </button>
        <div className="request-modal-head">
          {posterPath !== undefined && <img className="request-modal-poster" src={posterUrl(posterPath)} alt="" />}
          <div>
            <h2 id="request-modal-title" className="request-modal-title">
              {title}
            </h2>
            {subtitle && <p className="request-modal-sub">{subtitle}</p>}
          </div>
        </div>
        <div className="request-modal-best">
          <Icon name="hd" />
          <div>
            <b>The best copy out there</b>
            <small>4K when it exists, otherwise the sharpest release within the household's download limits.</small>
          </div>
        </div>
        <div className="request-modal-notify">
          <div>
            <b>Notify me when it lands</b>
            <small>A note in the bell, and a push to any device that signed up.</small>
          </div>
          <Toggle checked={notifyOn} onChange={setNotify} label="Notify me when it lands" />
        </div>
        <div className="request-modal-foot">
          <span className="request-modal-note">{free != null ? `${formatBytes(free)} free.` : 'Free space unknown.'}</span>
          <button className="request-modal-cancel" onClick={onClose}>
            Cancel
          </button>
          <button className="request-modal-submit" onClick={() => onSubmit(notifyOn)}>
            <Icon name="plus" />
            {submitLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
