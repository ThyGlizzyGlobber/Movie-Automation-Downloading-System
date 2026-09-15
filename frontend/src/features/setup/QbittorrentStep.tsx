import { useState } from 'react'
import { setupQbt, testQbtConnection } from '../../api/setup'
import { ApiError } from '../../api/client'

export default function QbittorrentStep({
  setupToken,
  onDone,
}: {
  setupToken: string
  onDone: () => void
}) {
  const [host, setHost] = useState('')
  const [port, setPort] = useState('8080')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const body = { host: host.trim(), port: Number(port), username: username.trim(), password }
  const canSubmit = host.trim().length > 0 && Number(port) > 0

  async function test() {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await testQbtConnection(body, setupToken)
      setTestResult(
        result.reachable
          ? { ok: true, message: 'Connected.' }
          : { ok: false, message: result.detail || "Couldn't connect. Check the details and try again." },
      )
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await setupQbt(body, setupToken)
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <h1 className="setup-step-title">Download client</h1>
      <p className="setup-step-copy">Where downloads run. Enter your qBittorrent details and test them.</p>
      <div className="setup-row">
        <div className="setup-field">
          <label htmlFor="qbt-host">Host</label>
          <input id="qbt-host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.0.10" />
        </div>
        <div className="setup-field" style={{ maxWidth: 100 }}>
          <label htmlFor="qbt-port">Port</label>
          <input id="qbt-port" value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" />
        </div>
      </div>
      <div className="setup-row">
        <div className="setup-field">
          <label htmlFor="qbt-username">Username</label>
          <input id="qbt-username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="setup-field">
          <label htmlFor="qbt-password">Password</label>
          <input
            id="qbt-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      </div>
      {testResult && (
        <p className="setup-error" style={testResult.ok ? { color: 'var(--status-complete)' } : undefined}>
          {testResult.message}
        </p>
      )}
      {error && <p className="setup-error">{error}</p>}
      <div className="setup-actions">
        <button className="setup-button secondary" disabled={!canSubmit || testing} onClick={test}>
          {testing ? 'Testing…' : 'Test connection'}
        </button>
        <button className="setup-button primary" disabled={!canSubmit || saving} onClick={save}>
          {saving ? 'Saving…' : 'Continue'}
        </button>
      </div>
    </>
  )
}
