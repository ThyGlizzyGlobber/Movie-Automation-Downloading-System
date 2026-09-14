import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getAuditLog, getRemoteAccess, revokeAllSessions, setRemoteAccess } from '../../api/settings'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { ApiError } from '../../api/client'
import './RemoteAccessPanel.css'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

function RemoteAccessSettingsSection() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['settings', 'remote-access'], queryFn: getRemoteAccess })
  const [enabled, setEnabled] = useState(false)
  const [domain, setDomain] = useState('')
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!query.data) return
    setEnabled(query.data.remote_access_enabled)
    setDomain(query.data.public_domain ?? '')
  }, [query.data])

  if (query.isLoading) return <LoadingState />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }

  async function save() {
    setSaveError(null)
    setSaveState('saving')
    try {
      await setRemoteAccess({ remote_access_enabled: enabled, public_domain: domain.trim() || null })
      queryClient.invalidateQueries({ queryKey: ['settings', 'remote-access'] })
      // This is itself an audited event (record_auth_event's own
      // "remote_access_toggled") — refresh the log below so it shows up
      // without needing to leave and come back to this same panel.
      queryClient.invalidateQueries({ queryKey: ['audit-log'] })
      setSaveState('saved')
      setTimeout(() => setSaveState('idle'), 1200)
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : 'Something went wrong.')
      setSaveState('error')
    }
  }

  return (
    <div className="settings-panel-card" style={{ marginBottom: 16 }}>
      <h2>Remote access</h2>
      <div className="settings-hint" style={{ marginTop: 0 }}>
        This app never opens a port on its own — set up a reverse proxy (Traefik, Nginx Proxy Manager, or Cloudflare
        Tunnel, which needs no inbound port-forward at all) in front of it first. Turning this on only tells the
        backend that HTTPS is genuinely in place, so it can mark your session cookie <code>Secure</code>.
      </div>
      <div className="settings-field">
        <label className="settings-checkbox-label">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Reachable from outside the LAN
        </label>
      </div>
      <div className="settings-field">
        <label htmlFor="raPublicDomain">Public domain (optional)</label>
        <input
          id="raPublicDomain"
          type="text"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="smithflix.example.com"
        />
        <div className="settings-hint">Just for your own reference here — confirms what you've pointed the proxy at.</div>
      </div>
      <button className="settings-btn" disabled={saveState === 'saving'} onClick={save}>
        {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save'}
      </button>
      {saveState === 'error' && saveError && <div className="settings-save-error">{saveError}</div>}
    </div>
  )
}

function RevokeSessionsSection() {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  async function revoke() {
    if (!confirm('Sign out every device, including this one? Everyone — you included — will need to sign in again.')) {
      return
    }
    setBusy(true)
    setResult(null)
    try {
      const { revoked } = await revokeAllSessions()
      setResult({ ok: true, message: `Signed out ${revoked} session(s).` })
      queryClient.invalidateQueries({ queryKey: ['session'] })
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="settings-panel-card" style={{ marginBottom: 16 }}>
      <h2>Lost a device?</h2>
      <div className="settings-hint" style={{ marginTop: 0 }}>
        Immediately signs everyone out, this session included — the fastest way to cut off access from a lost or
        stolen device without waiting for its session to expire on its own.
      </div>
      <button className="settings-btn danger" disabled={busy} onClick={revoke}>
        {busy ? 'Signing everyone out…' : 'Sign out every device'}
      </button>
      {result && <div className={result.ok ? 'settings-save-success' : 'settings-save-error'}>{result.message}</div>}
    </div>
  )
}

function eventLabel(eventType: string): string {
  switch (eventType) {
    case 'login_success':
      return 'Signed in'
    case 'login_failure':
      return 'Sign-in refused'
    case 'plex_server_selected':
      return 'Plex server linked/switched'
    case 'plex_unlinked':
      return 'Plex disconnected'
    case 'tmdb_key_changed':
      return 'TMDB key changed'
    case 'qbt_connection_changed':
      return 'qBittorrent connection changed'
    case 'remote_access_toggled':
      return 'Remote access changed'
    case 'sessions_revoked':
      return 'All sessions revoked'
    case 'deploy_triggered':
      return 'Update deployed'
    case 'deploy_failed':
      return 'Update failed'
    default:
      return eventType
  }
}

function AuditLogSection() {
  const query = useQuery({ queryKey: ['audit-log'], queryFn: () => getAuditLog(20, 0) })

  if (query.isLoading) return <LoadingState />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }
  const data = query.data!

  return (
    <div className="settings-panel-card">
      <h2>Recent security events</h2>
      {data.events.length === 0 ? (
        <EmptyState message="Nothing logged yet." />
      ) : (
        <div className="audit-log-list">
          {data.events.map((e) => (
            <div className="audit-log-row" key={e.id}>
              <div className="audit-log-main">
                <span className="audit-log-type">{eventLabel(e.event_type)}</span>
                <span className="audit-log-sub">
                  {[e.username, e.ip_address, e.detail].filter(Boolean).join(' · ') || '—'}
                </span>
              </div>
              <span className="audit-log-time">
                {new Date(e.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function RemoteAccessPanel() {
  return (
    <>
      <RemoteAccessSettingsSection />
      <RevokeSessionsSection />
      <AuditLogSection />
    </>
  )
}
