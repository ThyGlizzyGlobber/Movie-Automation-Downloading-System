import { useState } from 'react'
import type { RedownloadMode } from '../types/requests'
import './RedownloadModal.css'

// Part K1 — shown instead of immediately firing a request whenever the
// target is already on Plex. "Overwrite" gets its own second confirm
// step since it deletes a file; it's only offered at all when the caller
// has a confirmed on_plex_tracked record for this title (never against a
// file only the fuzzy on_plex title/year match found).
export default function RedownloadModal({
  open,
  targetLabel,
  trackedAvailable,
  onChoose,
  onClose,
}: {
  open: boolean
  targetLabel: string
  trackedAvailable: boolean
  onChoose: (mode: RedownloadMode) => void
  onClose: () => void
}) {
  const [confirmingOverwrite, setConfirmingOverwrite] = useState(false)

  if (!open) return null

  function close() {
    setConfirmingOverwrite(false)
    onClose()
  }

  if (confirmingOverwrite) {
    return (
      <div className="redownload-overlay" onClick={close}>
        <div className="redownload-card" onClick={(e) => e.stopPropagation()}>
          <h2 className="redownload-title">Overwrite existing file?</h2>
          <p className="redownload-body">
            This will permanently delete the existing copy of {targetLabel} once the new download completes. This can't be undone.
          </p>
          <div className="redownload-actions">
            <button
              className="redownload-option danger"
              onClick={() => {
                setConfirmingOverwrite(false)
                onChoose('overwrite')
              }}
            >
              Yes, overwrite it
            </button>
            <button className="redownload-cancel" onClick={close}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="redownload-overlay" onClick={close}>
      <div className="redownload-card" onClick={(e) => e.stopPropagation()}>
        <h2 className="redownload-title">Already on Plex</h2>
        <p className="redownload-body">{targetLabel} is already on your Plex server. What would you like to do?</p>
        <div className="redownload-actions">
          <button className="redownload-option" onClick={() => onChoose('upgrade')}>
            Find a better version
            <span className="redownload-option-sub">Search again and add it alongside what's already there.</span>
          </button>
          <button
            className="redownload-option"
            disabled={!trackedAvailable}
            title={trackedAvailable ? undefined : "This app doesn't have a record of placing the existing file, so it can't safely delete it."}
            onClick={() => setConfirmingOverwrite(true)}
          >
            Overwrite existing
            <span className="redownload-option-sub">
              {trackedAvailable ? 'Replace the existing copy once the new one finishes.' : 'Unavailable — this file was not added by this app.'}
            </span>
          </button>
        </div>
        <button className="redownload-cancel" onClick={close}>
          Cancel
        </button>
      </div>
    </div>
  )
}
