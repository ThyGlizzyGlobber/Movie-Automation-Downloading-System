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
          <h2 className="redownload-title">Replace the current copy?</h2>
          <p className="redownload-body">
            The current copy of {targetLabel} is deleted once the new download finishes. This can't be undone.
          </p>
          <div className="redownload-actions">
            <button
              className="redownload-option danger"
              onClick={() => {
                setConfirmingOverwrite(false)
                onChoose('overwrite')
              }}
            >
              Yes, replace it
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
        <h2 className="redownload-title">Already in Plex</h2>
        <p className="redownload-body">{targetLabel} is already in Plex. What would you like to do?</p>
        <div className="redownload-actions">
          <button className="redownload-option" onClick={() => onChoose('upgrade')}>
            Find a better version
            <span className="redownload-option-sub">Search again and keep both copies.</span>
          </button>
          <button
            className="redownload-option"
            disabled={!trackedAvailable}
            title={trackedAvailable ? undefined : "Meridian didn't add this file, so it won't delete it."}
            onClick={() => setConfirmingOverwrite(true)}
          >
            Replace it
            <span className="redownload-option-sub">
              {trackedAvailable ? 'Delete the current copy when the new one is ready.' : "Not available. Meridian didn't add this file."}
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
