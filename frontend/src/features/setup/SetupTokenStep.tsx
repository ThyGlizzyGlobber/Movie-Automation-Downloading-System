import { useState } from 'react'

export default function SetupTokenStep({ onContinue }: { onContinue: (token: string) => void }) {
  const [token, setToken] = useState('')

  return (
    <>
      <h1 className="setup-step-title">Let's set this up</h1>
      <p className="setup-step-copy">
        Check the backend container's logs for a one-time setup code (<code>docker logs</code> the backend
        service), then paste it below. This confirms it's really you setting this up, on this network.
      </p>
      <div className="setup-field">
        <label htmlFor="setup-token">Setup code</label>
        <input
          id="setup-token"
          type="text"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Paste the code from the logs"
        />
      </div>
      <div className="setup-actions">
        <button className="setup-button primary" disabled={!token.trim()} onClick={() => onContinue(token.trim())}>
          Continue
        </button>
      </div>
    </>
  )
}
