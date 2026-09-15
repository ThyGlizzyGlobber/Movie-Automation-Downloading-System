import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { getPlexStatus, startPlexLink } from '../../api/plex'
import { ApiError } from '../../api/client'

const POLL_INTERVAL_MS = 2500

type Phase = 'idle' | 'signing-in' | 'linking'

export default function PlexLinkStep({ setupToken }: { setupToken: string }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const queryClient = useQueryClient()

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  // PlexLinker._poll() (backend) already resolves and persists the
  // account's one owned server as soon as the PIN is approved — the same
  // auto-selection PlexServerPanel's own "Sign in with Plex" already
  // relies on (it treats `status.linked && !status.pending` as fully
  // done, no separate picker). This step used to also call
  // GET /api/plex/servers + PUT /api/plex/server here, expecting a
  // distinct "pick a server" step — but by the time that ran,
  // plex_server_machine_id was already set (by _poll() itself), which
  // closes require_admin_or_setup_bootstrap's setup-token window, so
  // that follow-up call 401'd and setup silently finished server-side
  // while this screen sat stuck showing "waiting for approval." Matching
  // PlexServerPanel's own handling avoids the race entirely.
  async function complete() {
    setPhase('linking')
    queryClient.invalidateQueries({ queryKey: ['setupStatus'] })
    queryClient.invalidateQueries({ queryKey: ['session'] })
  }

  async function begin() {
    setError(null)
    setPhase('signing-in')
    try {
      const { auth_url } = await startPlexLink(setupToken)
      window.open(auth_url, '_blank', 'noopener,noreferrer')
      timerRef.current = window.setInterval(async () => {
        try {
          const status = await getPlexStatus(setupToken)
          if (status.error) {
            setError(status.error)
            stopPolling()
            setPhase('idle')
            return
          }
          if (status.linked && !status.pending) {
            stopPolling()
            await complete()
          }
        } catch (err) {
          setError(err instanceof ApiError ? err.message : 'Something went wrong.')
          stopPolling()
          setPhase('idle')
        }
      }, POLL_INTERVAL_MS)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
      setPhase('idle')
    }
  }

  return (
    <>
      <h1 className="setup-step-title">Connect Plex</h1>
      <p className="setup-step-copy">Sign in with the Plex account that owns your server. That account becomes the admin.</p>

      {phase === 'idle' && (
        <div className="setup-actions">
          <button className="setup-button primary" onClick={begin}>
            Sign in with Plex
          </button>
        </div>
      )}

      {phase === 'signing-in' && (
        <p className="setup-step-copy">
          Finish signing in in the tab that opened. This page updates by itself.
        </p>
      )}

      {phase === 'linking' && <p className="setup-step-copy">Finishing setup…</p>}

      {error && <p className="setup-error">{error}</p>}
    </>
  )
}
