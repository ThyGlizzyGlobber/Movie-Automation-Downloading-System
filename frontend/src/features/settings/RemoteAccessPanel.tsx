import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getAuditLog, getRemoteAccess, revokeAllSessions, setRemoteAccess } from '../../api/settings'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { ApiError } from '../../api/client'
import SettingRow from '../../components/SettingRow'
import Toggle from '../../components/Toggle'
import './RemoteAccessPanel.css'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

const REMOTE_SUB =
  'A record of where Obsidian is published — for example through a Cloudflare Tunnel. Sign-ins protect themselves per request, so nothing here decides whether you can sign in.'
const HISTORY_SUB = 'Recent sign-ins and changes to access.'

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

  if (query.isLoading) return <SettingsCardSkeleton title="Remote access" sub={REMOTE_SUB} rows={2} style={{ marginBottom: 16 }} />
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
      <p className="settings-sub">{REMOTE_SUB}</p>
      <SettingRow label="Obsidian is reachable from outside the house" hint="Noted here and in the history below.">
        <Toggle checked={enabled} onChange={setEnabled} label="Obsidian is reachable from outside the house" />
      </SettingRow>
      <SettingRow label="Web address" hint="A note of where you've pointed it. Nothing else uses this." htmlFor="raPublicDomain">
        <input
          id="raPublicDomain"
          className="mono"
          type="text"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="obsidian.example.com"
          style={{ width: 240 }}
        />
      </SettingRow>
      <div className="settings-btn-row" style={{ marginTop: 18 }}>
        <button className="settings-btn" disabled={saveState === 'saving'} onClick={save}>
          {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save'}
        </button>
      </div>
      {saveState === 'error' && saveError && <div className="settings-save-error">{saveError}</div>}
    </div>
  )
}

function RevokeSessionsSection() {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  async function revoke() {
    if (!confirm('Sign out every device, including this one?')) {
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
      <p className="settings-sub">Cut off a lost or stolen device.</p>
      <SettingRow label="Sign out every device" hint="Signs everyone out straight away, including you.">
        <button className="settings-btn danger sm" disabled={busy} onClick={revoke}>
          {busy ? 'Signing out…' : 'Sign out all'}
        </button>
      </SettingRow>
      {result && <div className={result.ok ? 'settings-save-success' : 'settings-save-error'}>{result.message}</div>}
    </div>
  )
}

function eventLabel(eventType: string): string {
  switch (eventType) {
    case 'login_success':
      return 'Signed in'
    case 'login_failure':
      return 'Sign-in blocked'
    case 'plex_server_selected':
      return 'Plex server changed'
    case 'plex_unlinked':
      return 'Plex disconnected'
    case 'tmdb_key_changed':
      return 'Movie & TV info key changed'
    case 'qbt_connection_changed':
      return 'Download client changed'
    case 'remote_access_toggled':
      return 'Remote access changed'
    case 'sessions_revoked':
      return 'Everyone signed out'
    case 'deploy_triggered':
      return 'Updated'
    case 'deploy_failed':
      return 'Update failed'
    default:
      return eventType
  }
}

function AuditLogSection() {
  const query = useQuery({ queryKey: ['audit-log'], queryFn: () => getAuditLog(20, 0) })

  if (query.isLoading) return <SettingsCardSkeleton title="Sign-in history" sub={HISTORY_SUB} rows={5} />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }
  const data = query.data!

  return (
    <div className="settings-panel-card">
      <h2>Sign-in history</h2>
      <p className="settings-sub">{HISTORY_SUB}</p>
      {data.events.length === 0 ? (
        <EmptyState message="Nothing logged yet." />
      ) : (
        <div className="audit-log-list">
          {data.events.map((e) => (
            <div className="audit-log-row" key={e.id}>
              <div className="audit-log-main">
                <span className="audit-log-type">{eventLabel(e.event_type)}</span>
                <span className="audit-log-sub">
                  {[e.username, e.ip_address, e.detail].filter(Boolean).join(' | ') || '—'}
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
