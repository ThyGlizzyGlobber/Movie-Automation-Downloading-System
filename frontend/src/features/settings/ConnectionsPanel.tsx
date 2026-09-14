import { useState } from 'react'
import { useSetupStatus } from '../setup/useSetupStatus'
import { updateTmdbSettings, testQbtSettingsConnection, updateQbtSettings } from '../../api/settings'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { ApiError } from '../../api/client'

function TmdbSection({ envConfigured }: { envConfigured: boolean }) {
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  async function save() {
    setSaving(true)
    setResult(null)
    try {
      await updateTmdbSettings(apiKey.trim())
      setResult({ ok: true, message: 'Saved — restart the backend for this to take effect.' })
      setApiKey('')
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <h2>TMDB</h2>
      {envConfigured ? (
        <div className="settings-env-note">Configured via environment variable — edit it in the backend's own .env to change it.</div>
      ) : (
        <>
          <div className="settings-field">
            <label htmlFor="cxTmdbKey">API key</label>
            <input
              id="cxTmdbKey"
              type="text"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Paste a new TMDB API key to replace it"
            />
          </div>
          <button className="settings-btn" disabled={!apiKey.trim() || saving} onClick={save}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {result && <div className={result.ok ? 'settings-save-success' : 'settings-save-error'}>{result.message}</div>}
        </>
      )}
    </>
  )
}

function QbittorrentSection({ envConfigured }: { envConfigured: boolean }) {
  const [host, setHost] = useState('')
  const [port, setPort] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState<{ ok: boolean; message: string } | null>(null)

  const body = { host: host.trim(), port: Number(port), username: username.trim(), password }
  const canSubmit = host.trim().length > 0 && Number(port) > 0

  async function test() {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await testQbtSettingsConnection(body)
      setTestResult(
        result.reachable ? { ok: true, message: 'Connected.' } : { ok: false, message: result.detail || "Couldn't reach qBittorrent with these details." },
      )
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    setSaving(true)
    setSaveResult(null)
    try {
      await updateQbtSettings(body)
      setSaveResult({ ok: true, message: 'Saved — restart the backend for this to take effect.' })
    } catch (err) {
      setSaveResult({ ok: false, message: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <h2>qBittorrent</h2>
      {envConfigured ? (
        <div className="settings-env-note">
          Configured via environment variables — edit them in the backend's own .env to change them.
        </div>
      ) : (
        <>
          <div className="settings-row">
            <div className="settings-field">
              <label htmlFor="cxQbtHost">Host</label>
              <input id="cxQbtHost" value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.0.10" />
            </div>
            <div className="settings-field" style={{ maxWidth: 110 }}>
              <label htmlFor="cxQbtPort">Port</label>
              <input id="cxQbtPort" value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" placeholder="8080" />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-field">
              <label htmlFor="cxQbtUsername">Username</label>
              <input id="cxQbtUsername" value={username} onChange={(e) => setUsername(e.target.value)} />
            </div>
            <div className="settings-field">
              <label htmlFor="cxQbtPassword">Password</label>
              <input id="cxQbtPassword" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Leave blank to keep unchanged" />
            </div>
          </div>
          <div className="settings-btn-row">
            <button className="settings-btn secondary" disabled={!canSubmit || testing} onClick={test}>
              {testing ? 'Testing…' : 'Test connection'}
            </button>
            <button className="settings-btn" disabled={!canSubmit || saving} onClick={save}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {testResult && <div className={testResult.ok ? 'settings-save-success' : 'settings-save-error'}>{testResult.message}</div>}
          {saveResult && <div className={saveResult.ok ? 'settings-save-success' : 'settings-save-error'}>{saveResult.message}</div>}
        </>
      )}
    </>
  )
}

export default function ConnectionsPanel() {
  const setupStatus = useSetupStatus()

  if (setupStatus.isLoading) return <LoadingState />
  if (setupStatus.isError) {
    return <ErrorState message={setupStatus.error instanceof Error ? setupStatus.error.message : undefined} />
  }

  const data = setupStatus.data!

  return (
    <>
      <div className="settings-panel-card" style={{ marginBottom: 16 }}>
        <TmdbSection envConfigured={data.tmdb_source === 'env'} />
      </div>
      <div className="settings-panel-card">
        <QbittorrentSection envConfigured={data.qbt_source === 'env'} />
      </div>
    </>
  )
}
