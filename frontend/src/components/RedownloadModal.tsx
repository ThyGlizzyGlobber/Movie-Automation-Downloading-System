import { useState } from 'react'
import type { RedownloadMode } from '../types/requests'
import './RedownloadModal.css'

export type RedownloadChoice = RedownloadMode | 'reject'

// Part K1 — shown instead of immediately firing a request whenever the
// target is already on Plex. "Overwrite" and "broken copy" each get a
// second confirm step since they delete a file; both are only offered
// when the caller has a confirmed on_plex_tracked record for this title
// (never against a file only the fuzzy on_plex title/year match found).
// "Broken copy" also blacklists that exact release so the next search
// can't pick it again.
export default function RedownloadModal({
  open,
  targetLabel,
  trackedAvailable,
  canReject = false,
  onChoose,
  onClose,
}: {
  open: boolean
  targetLabel: string
  trackedAvailable: boolean
  /* Offer "This copy is broken" (movies: one file, one release to rule out). */
  canReject?: boolean
  onChoose: (mode: RedownloadChoice) => void
  onClose: () => void
}) {
  const [confirming, setConfirming] = useState<'overwrite' | 'reject' | null>(null)

  if (!open) return null

  function close() {
    setConfirming(null)
    onClose()
  }

  if (confirming) {
    const reject = confirming === 'reject'
    return (
      <div className="redownload-overlay" onClick={close}>
        <div className="redownload-card" onClick={(e) => e.stopPropagation()}>
          <h2 className="redownload-title">{reject ? 'Bin this copy and get another?' : 'Replace the current copy?'}</h2>
          <p className="redownload-body">
            {reject
              ? `The current copy of ${targetLabel} is deleted now and never picked again; a different copy is fetched. This can't be undone.`
              : `The current copy of ${targetLabel} is deleted once the new download finishes. This can't be undone.`}
          </p>
          <div className="redownload-actions">
            <button
              className="redownload-option danger"
              onClick={() => {
                setConfirming(null)
                onChoose(reject ? 'reject' : 'overwrite')
              }}
            >
              {reject ? 'Yes, get a different copy' : 'Yes, replace it'}
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
        <p className="redownload-body">{targetLabel} is already on Plex. What would you like to do?</p>
        <div className="redownload-actions">
          <button className="redownload-option" onClick={() => onChoose('upgrade')}>
            Find a better version
            <span className="redownload-option-sub">Search again and keep both copies.</span>
          </button>
          <button
            className="redownload-option"
            disabled={!trackedAvailable}
            title={trackedAvailable ? undefined : "Meridian didn't add this file, so it won't delete it."}
            onClick={() => setConfirming('overwrite')}
          >
            Replace it
            <span className="redownload-option-sub">
              {trackedAvailable ? 'Delete the current copy when the new one is ready.' : "Not available. Meridian didn't add this file."}
            </span>
          </button>
          {canReject && (
          <button
            className="redownload-option"
            disabled={!trackedAvailable}
            title={trackedAvailable ? undefined : "Meridian didn't add this file, so it can't rule it out."}
            onClick={() => setConfirming('reject')}
          >
            This copy is broken
            <span className="redownload-option-sub">
              {trackedAvailable ? 'Bad audio, wrong cut, won\'t play: bin it and never pick this release again.' : "Not available. Meridian didn't add this file."}
            </span>
          </button>
          )}
        </div>
        <button className="redownload-cancel" onClick={close}>
          Cancel
        </button>
      </div>
    </div>
  )
}
