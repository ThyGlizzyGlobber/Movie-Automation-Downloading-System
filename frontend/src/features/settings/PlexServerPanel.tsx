import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addPlexServer,
  getPlexStatus,
  labelPlexServer,
  listLinkedServers,
  listPlexServers,
  removePlexServer,
  reorderPlexServers,
  startPlexLink,
  unlinkPlex,
} from '../../api/plex'
import type { PlexServerSummary } from '../../types/auth'
import type { LinkedPlexServer } from '../../types/settings'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import ErrorState from '../../components/ui/ErrorState'
import { ApiError } from '../../api/client'
import Icon from '../../components/ui/Icon'
import LibraryRows from './LibraryRows'
import { useSession } from '../auth/useSession'
import { useSwitchServer } from '../../lib/useSwitchServer'

const POLL_INTERVAL_MS = 2500

type Phase = 'status' | 'signing-in' | 'adding'

// The redesigned Settings' own version of the setup wizard's PlexLinkStep
// — same PIN sign-in flow, but starting from "already linked, show
// status" — and the list of servers Obsidian serves. Each server's own
// sharing decides who signs in to it; someone shared on more than one
// lands on the first in this list, and an admin views whichever they pick.
const PLEX_SUB = 'The Plex servers Obsidian adds to and reads from. Each one keeps its own requests.'

export default function PlexServerPanel() {
  const queryClient = useQueryClient()
  const statusQuery = useQuery({ queryKey: ['plexStatus'], queryFn: () => getPlexStatus() })
  const linked = statusQuery.data?.linked ?? false
  const serversQuery = useQuery({ queryKey: ['linkedServers'], queryFn: listLinkedServers, enabled: linked })
  const session = useSession()
  const { switchTo, switching } = useSwitchServer()
  const [phase, setPhase] = useState<Phase>('status')
  const [error, setError] = useState<string | null>(null)
  const [addable, setAddable] = useState<PlexServerSummary[]>([])
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
    queryClient.invalidateQueries({ queryKey: ['linkedServers'] })
  }

  // After the list changes: the session carries every server's name for
  // the switcher, and the household and sign-in history name them too.
  function changed(list: LinkedPlexServer[]) {
    queryClient.setQueryData(['linkedServers'], list)
    queryClient.invalidateQueries({ queryKey: ['session'] })
    queryClient.invalidateQueries({ queryKey: ['household'] })
    queryClient.invalidateQueries({ queryKey: ['audit-log'] })
  }

  async function run(action: () => Promise<LinkedPlexServer[]>, failure: string) {
    setError(null)
    setBusy(true)
    try {
      changed(await action())
      return true
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failure)
      return false
    } finally {
      setBusy(false)
    }
  }

  async function loadAddable() {
    setError(null)
    setBusy(true)
    try {
      const list = (await listPlexServers()).filter((s) => !s.linked)
      setAddable(list)
      setSelected(list.length === 1 ? list[0].machine_identifier : null)
      setPhase('adding')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load your servers.")
    } finally {
      setBusy(false)
    }
  }

  async function finishAdd() {
    if (!selected) return
    if (await run(() => addPlexServer(selected), "Couldn't add that server.")) setPhase('status')
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
    if (!confirm('Disconnect Plex? Obsidian stops working for everyone until an admin connects it again.')) return
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

  function remove(server: LinkedPlexServer) {
    const ok = confirm(
      `Remove ${server.display_name}? Everyone signed in through it is signed out. What only it asked for stops showing, and shows only it follows stop being checked.`,
    )
    if (ok) run(() => removePlexServer(server.id), "Couldn't remove that server.")
  }

  if (statusQuery.isLoading) return <SettingsCardSkeleton title="Plex" sub={PLEX_SUB} rows={3} />
  if (statusQuery.isError) {
    return <ErrorState message={statusQuery.error instanceof Error ? statusQuery.error.message : undefined} />
  }
  const status = statusQuery.data!
  const servers = serversQuery.data ?? []
  const viewing = session.data?.server?.id

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
                  <b>{status.username || 'Your Plex account'}</b>
                  <small>Signed in to Plex | {servers.length === 1 ? '1 server' : `${servers.length} servers`}</small>
                </div>
                <div className="settings-btn-row">
                  <button className="settings-btn secondary sm" disabled={busy} onClick={loadAddable}>
                    Add server
                  </button>
                  <button className="settings-btn ghost sm" disabled={busy} onClick={disconnect}>
                    Disconnect
                  </button>
                </div>
              </div>
              <div className="household-list plex-server-rows">
                {servers.map((server, index) => (
                  <ServerRow
                    key={server.id}
                    server={server}
                    primary={index === 0}
                    viewing={server.id === viewing}
                    several={servers.length > 1}
                    busy={busy || switching !== null}
                    onLabel={(label) => run(() => labelPlexServer(server.id, label), "Couldn't rename that server.")}
                    onMakePrimary={() =>
                      run(
                        () => reorderPlexServers([server.id, ...servers.filter((s) => s.id !== server.id).map((s) => s.id)]),
                        "Couldn't reorder the servers.",
                      )
                    }
                    onView={() => switchTo(server.id, server.display_name)}
                    onRemove={() => remove(server)}
                  />
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="plex-hint">Sign in with the Plex account that owns your servers.</div>
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

      {phase === 'adding' && (
        <>
          {addable.length === 0 ? (
            <div className="plex-hint">Every server this Plex account owns is already here.</div>
          ) : (
            <>
              <p className="plex-hint">
                Pick a server this account owns. Nobody is signed out; people shared on it reach it from their next sign-in.
              </p>
              <div className="settings-server-list">
                {addable.map((s) => (
                  <button
                    key={s.machine_identifier}
                    className={`settings-server-option${selected === s.machine_identifier ? ' selected' : ''}`}
                    onClick={() => setSelected(s.machine_identifier)}
                  >
                    {s.name}
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="settings-btn-row">
            <button className="settings-btn secondary" onClick={() => setPhase('status')}>
              Cancel
            </button>
            {addable.length > 0 && (
              <button className="settings-btn" disabled={!selected || busy} onClick={finishAdd}>
                Add
              </button>
            )}
          </div>
        </>
      )}

      {error && <div className="plex-error">{error}</div>}

      {status.linked && phase === 'status' && <LibraryRows part="library" />}
    </div>
  )
}

function ServerRow({
  server,
  primary,
  viewing,
  several,
  busy,
  onLabel,
  onMakePrimary,
  onView,
  onRemove,
}: {
  server: LinkedPlexServer
  primary: boolean
  viewing: boolean
  several: boolean
  busy: boolean
  onLabel: (label: string) => void
  onMakePrimary: () => void
  onView: () => void
  onRemove: () => void
}) {
  // The household's name for it ("Immediate family", "Friends"), edited
  // in place; cleared, it goes back to Plex's own name for the server.
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState('')
  // Enter saves and closes the field, and the field closing blurs it:
  // only the first of the two counts.
  const finished = useRef(false)

  function startEditing() {
    setLabel(server.label ?? '')
    finished.current = false
    setEditing(true)
  }
  function finish(save: boolean) {
    if (finished.current) return
    finished.current = true
    setEditing(false)
    if (save && label.trim() !== (server.label ?? '')) onLabel(label.trim())
  }

  const people = server.members === 1 ? '1 person' : `${server.members} people`
  // A plain setting row, unlike the household's: with up to four buttons
  // it stacks under the name on a phone rather than squeezing it.
  return (
    <div className="setting-row">
      <div className="setting-row-text">
        {editing ? (
          <div className="setting-row-control">
            <input
              autoFocus
              onFocus={(e) => e.currentTarget.select()}
              value={label}
              maxLength={60}
              placeholder={server.name ?? 'Label'}
              aria-label={`Name for ${server.display_name}`}
              onChange={(e) => setLabel(e.target.value)}
              onBlur={() => finish(true)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') finish(true)
                if (e.key === 'Escape') finish(false)
              }}
            />
          </div>
        ) : (
          <b>
            {server.display_name}
            {primary && several && <span className="household-tag">Primary</span>}
            {viewing && several && <span className="household-tag">Viewing</span>}
          </b>
        )}
        <small>
          {server.label && server.name ? `${server.name} on Plex | ` : ''}
          {people}
        </small>
      </div>
      <div className="setting-row-control">
        {several && !viewing && (
          <button className="settings-btn secondary sm" disabled={busy} onClick={onView}>
            View
          </button>
        )}
        {several && !primary && (
          <button className="settings-btn ghost sm" disabled={busy} onClick={onMakePrimary}>
            Make primary
          </button>
        )}
        <button className="settings-btn ghost sm" disabled={busy || editing} onClick={startEditing}>
          Rename
        </button>
        {several && (
          <button className="rq-circ danger" aria-label={`Remove ${server.display_name}`} disabled={busy} onClick={onRemove}>
            <Icon name="trash" />
          </button>
        )}
      </div>
    </div>
  )
}
