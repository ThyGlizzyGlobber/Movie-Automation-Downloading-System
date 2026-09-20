import { useState } from 'react'
import { setupTmdb } from '../../api/setup'
import { ApiError } from '../../api/client'

export default function TmdbStep({
  setupToken,
  onDone,
}: {
  setupToken: string
  onDone: () => void
}) {
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await setupTmdb(apiKey.trim(), setupToken)
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <h1 className="setup-step-title">Movie &amp; TV info</h1>
      <p className="setup-step-copy">
        Obsidian gets titles, posters and details from TMDB. A free key takes a minute at{' '}
        <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noreferrer">
          themoviedb.org
        </a>
        .
      </p>
      <div className="setup-field">
        <label htmlFor="tmdb-key">API key</label>
        <input
          id="tmdb-key"
          type="text"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="Paste your key"
        />
      </div>
      {error && <p className="setup-error">{error}</p>}
      <div className="setup-actions">
        <button className="setup-button primary" disabled={!apiKey.trim() || saving} onClick={save}>
          {saving ? 'Saving…' : 'Continue'}
        </button>
      </div>
    </>
  )
}
