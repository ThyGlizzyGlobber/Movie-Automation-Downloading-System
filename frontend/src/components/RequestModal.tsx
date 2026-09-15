import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getQualityProfiles } from '../api/requests'
import { getStorage } from '../api/system'
import { formatBytes } from '../lib/format'
import { posterUrl } from '../lib/tmdbImage'
import type { QualityProfile } from '../types/features'
import Icon from './Icon'
import './RequestModal.css'

// The reference's request sheet: pick a named quality profile (Settings ›
// Quality profiles), see roughly how big that tends to be and how much
// room the library has, then request. One-tap paths elsewhere (the
// search palette) skip this and use the default profile.
export default function RequestModal({
  open,
  title,
  subtitle,
  posterPath,
  submitLabel = 'Request',
  onClose,
  onSubmit,
}: {
  open: boolean
  title: string
  subtitle?: string
  posterPath?: string | null
  submitLabel?: string
  onClose: () => void
  onSubmit: (profile: QualityProfile) => void
}) {
  const profiles = useQuery({ queryKey: ['quality-profiles'], queryFn: getQualityProfiles, enabled: open, staleTime: 60_000 })
  const storage = useQuery({ queryKey: ['storage'], queryFn: getStorage, enabled: open, staleTime: 60_000 })
  const [chosen, setChosen] = useState<string | null>(null)

  useEffect(() => {
    if (open) setChosen(null)
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
  const list = profiles.data?.profiles ?? []
  const selectedId = chosen ?? profiles.data?.default_profile_id ?? list[0]?.id ?? null
  const selected = list.find((p) => p.id === selectedId) ?? null
  const free = storage.data?.available ? storage.data.free_bytes : null
  const tooBig = selected?.typical_size_gb != null && free != null && selected.typical_size_gb * 1e9 > free

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
        <div className="request-modal-label">Quality</div>
        {profiles.isLoading ? (
          <div className="request-modal-loading">Loading…</div>
        ) : (
          <div className="request-modal-options" role="radiogroup" aria-label="Quality profile">
            {list.map((p) => (
              <button
                key={p.id}
                role="radio"
                aria-checked={p.id === selectedId}
                className={`request-modal-option${p.id === selectedId ? ' on' : ''}`}
                onClick={() => setChosen(p.id)}
              >
                <span className="request-modal-radio" />
                <span className="request-modal-option-text">
                  <b>{p.name}</b>
                  {p.description && <small>{p.description}</small>}
                </span>
                <span className="request-modal-size">{p.typical_size_gb != null ? `~${p.typical_size_gb} GB` : 'varies'}</span>
              </button>
            ))}
          </div>
        )}
        <div className="request-modal-foot">
          <span className={`request-modal-note${tooBig ? ' warn' : ''}`}>
            {free != null
              ? tooBig
                ? `Only ${formatBytes(free)} free. This usually needs more.`
                : `${formatBytes(free)} free.`
              : 'Free space unknown.'}
          </span>
          <button className="request-modal-cancel" onClick={onClose}>
            Cancel
          </button>
          <button className="request-modal-submit" disabled={!selected} onClick={() => selected && onSubmit(selected)}>
            <Icon name="plus" />
            {selected && selected.id !== 'default' ? `${submitLabel} ${selected.name}` : submitLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
