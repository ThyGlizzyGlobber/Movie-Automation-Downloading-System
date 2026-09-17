import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getPlexStatus, listPlexServers, selectPlexServer, startPlexLink, unlinkPlex } from '../../api/plex'
import type { PlexServerSummary } from '../../types/auth'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import ErrorState from '../../components/ErrorState'
import { ApiError } from '../../api/client'
import Icon from '../../components/Icon'
import LibraryRows from './LibraryRows'

const POLL_INTERVAL_MS = 2500

type Phase = 'status' | 'signing-in' | 'picking-server' | 'linking'

// The redesigned Settings' own version of the setup wizard's PlexLinkStep
// — same PIN sign-in flow, but starting from "already linked, show
// status" rather than a blank first-run, and adding the switch-server
// capability an admin with more than one Plex server needs (Part C1).
const PLEX_SUB = 'The server Meridian adds to and reads from.'

export default function PlexServerPanel() {
  const queryClient = useQueryClient()
  const statusQuery = useQuery({ queryKey: ['plexStatus'], queryFn: () => getPlexStatus() })
  const [phase, setPhase] = useState<Phase>('status')
  const [error, setError] = useState<string | null>(null)
  const [servers, setServers] = useState<PlexServerSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const timerRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])
  useEffect(() => stopPolling, [stopPolling])

  function refreshStatus() {
    queryClient.invalidateQueries({ queryKey: ['plexStatus'] })
  }

  async function loadServersForSwitch() {
    setError(null)
    setBusy(true)
    try {
      const list = await listPlexServers()
      setServers(list)
      setSelected(list.length === 1 ? list[0].machine_identifier : null)
      setPhase('picking-server')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load your servers.")
    } finally {
      setBusy(false)
    }
  }

  async function finishSwitch() {
    if (!selected) return
    if (!confirm('Switch servers? Everyone will be signed out and need to sign in again.')) return
    setPhase('linking')
    setError(null)
    try {
      await selectPlexServer(selected)
      refreshStatus()
      setPhase('status')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't switch servers.")
      setPhase('picking-server')
    }
  }

  async function beginSignIn() {
    setError(null)
    setPhase('signing-in')
    try {
      const { auth_url } = await startPlexLink()
      window.open(auth_url, '_blank', 'noopener,noreferrer')
      timerRef.current = window.setInterval(async () => {
        try {
          const status = await getPlexStatus()
          if (status.error) {
            setError(status.error)
            stopPolling()
            setPhase('status')
            return
          }
          if (status.linked && !status.pending) {
            stopPolling()
            refreshStatus()
            setPhase('status')
          }
        } catch (err) {
          setError(err instanceof ApiError ? err.message : 'Something went wrong.')
          stopPolling()
          setPhase('status')
        }
      }, POLL_INTERVAL_MS)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
      setPhase('status')
    }
  }

  async function disconnect() {
    if (!confirm('Disconnect Plex? Meridian stops working for everyone until an admin connects it again.')) return
    setBusy(true)
    try {
      await unlinkPlex()
      refreshStatus()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  if (statusQuery.isLoading) return <SettingsCardSkeleton title="Plex" sub={PLEX_SUB} rows={3} />
  if (statusQuery.isError) {
    return <ErrorState message={statusQuery.error instanceof Error ? statusQuery.error.message : undefined} />
  }
  const status = statusQuery.data!

  return (
    <div className="settings-panel-card">
      <h2>
        Plex
        {status.linked && phase === 'status' && (
          <span className="settings-live">
            <b />
            Connected
          </span>
        )}
      </h2>
      <p className="settings-sub">{PLEX_SUB}</p>

      {phase === 'status' && (
        <>
          {status.linked ? (
            <>
              <div className="settings-plex">
                <span className="settings-plex-mark">
                  <Icon name="plex" />
                </span>
                <div className="settings-plex-text">
                  <b>{status.server_name || 'Plex server'}</b>
                  <small>Signed in as {status.username || 'your account'}</small>
                </div>
                <div className="settings-btn-row">
                  <button className="settings-btn secondary sm" disabled={busy} onClick={loadServersForSwitch}>
                    Switch server
                  </button>
                  <button className="settings-btn ghost sm" disabled={busy} onClick={disconnect}>
                    Disconnect
                  </button>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="plex-hint">Sign in with the Plex account that owns your server.</div>
              <button className="settings-btn" disabled={status.pending} onClick={beginSignIn}>
                {status.pending ? 'Waiting for sign-in…' : 'Sign in with Plex'}
              </button>
            </>
          )}
        </>
      )}

      {phase === 'signing-in' && (
        <p className="plex-hint">Finish signing in in the tab that opened. This page updates by itself.</p>
      )}

      {phase === 'picking-server' && (
        <>
          {servers.length === 0 ? (
            <div className="plex-error">This account doesn't own a Plex server.</div>
          ) : (
            <div className="settings-server-list">
              {servers.map((s) => (
                <button
                  key={s.machine_identifier}
                  className={`settings-server-option${selected === s.machine_identifier ? ' selected' : ''}`}
                  onClick={() => setSelected(s.machine_identifier)}
                >
                  {s.name}
                </button>
              ))}
            </div>
          )}
          <div className="settings-btn-row">
            <button className="settings-btn secondary" onClick={() => setPhase('status')}>
              Cancel
            </button>
            <button className="settings-btn" disabled={!selected} onClick={finishSwitch}>
              Switch
            </button>
          </div>
        </>
      )}

      {phase === 'linking' && <p className="plex-hint">Switching servers…</p>}

      {error && <div className="plex-error">{error}</div>}

      {status.linked && phase === 'status' && <LibraryRows part="library" />}
    </div>
  )
}
