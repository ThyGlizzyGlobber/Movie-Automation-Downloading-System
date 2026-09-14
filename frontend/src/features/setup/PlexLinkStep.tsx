import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { getPlexStatus, listPlexServers, selectPlexServer, startPlexLink } from '../../api/plex'
import type { PlexServerSummary } from '../../types/auth'
import { ApiError } from '../../api/client'

const POLL_INTERVAL_MS = 2500

type Phase = 'idle' | 'signing-in' | 'picking-server' | 'linking'

export default function PlexLinkStep({ setupToken }: { setupToken: string }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [servers, setServers] = useState<PlexServerSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const queryClient = useQueryClient()

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  async function loadServers() {
    try {
      const list = await listPlexServers(setupToken)
      setServers(list)
      if (list.length === 1) {
        setSelected(list[0].machine_identifier)
      }
      setPhase('picking-server')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong loading your servers.')
    }
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
            await loadServers()
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

  async function finish() {
    if (!selected) return
    setPhase('linking')
    setError(null)
    try {
      await selectPlexServer(selected, setupToken)
      // Setup is genuinely complete now, and the response set a session
      // cookie for this account — both the setup-status gate and the
      // session itself need to reflect that on the next render.
      queryClient.invalidateQueries({ queryKey: ['setupStatus'] })
      queryClient.invalidateQueries({ queryKey: ['session'] })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong finishing setup.')
      setPhase('picking-server')
    }
  }

  return (
    <>
      <h1 className="setup-step-title">Link your Plex account</h1>
      <p className="setup-step-copy">
        Sign in with the Plex account that owns your server. This account becomes this app's admin.
      </p>

      {phase === 'idle' && (
        <div className="setup-actions">
          <button className="setup-button primary" onClick={begin}>
            Sign in with Plex
          </button>
        </div>
      )}

      {phase === 'signing-in' && (
        <p className="setup-step-copy">
          Approve the sign-in in the tab that just opened, then come back here — this updates on its own.
        </p>
      )}

      {phase === 'picking-server' && (
        <>
          {servers.length === 0 ? (
            <p className="setup-error">This account doesn't own any Plex servers.</p>
          ) : (
            <div className="setup-server-list">
              {servers.map((s) => (
                <button
                  key={s.machine_identifier}
                  className={`setup-server-option${selected === s.machine_identifier ? ' selected' : ''}`}
                  onClick={() => setSelected(s.machine_identifier)}
                >
                  {s.name}
                </button>
              ))}
            </div>
          )}
          <div className="setup-actions">
            <button className="setup-button primary" disabled={!selected} onClick={finish}>
              Finish setup
            </button>
          </div>
        </>
      )}

      {phase === 'linking' && <p className="setup-step-copy">Finishing setup…</p>}

      {error && <p className="setup-error">{error}</p>}
    </>
  )
}
