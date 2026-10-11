import { useState } from 'react'

// The card in a box's packaging carries its setup code, and a QR code for
// http://obsidian.local/#/?code=…, which lands here with the code filled in.
// Read once, when this module first loads, and taken out of the address bar
// then, so it isn't left in the browser's history. Not in the component: it
// can be mounted more than once, and only the first would find the code.
const CODE_FROM_LINK = (() => {
  const query = window.location.hash.split('?')[1]
  const code = (query ? new URLSearchParams(query).get('code') : null) ?? ''
  if (code) window.history.replaceState(null, '', `${window.location.pathname}#/`)
  return code
})()

export default function SetupTokenStep({ onContinue }: { onContinue: (token: string) => void }) {
  const [token, setToken] = useState(CODE_FROM_LINK)

  return (
    <>
      <h1 className="setup-step-title">Let's set this up</h1>
      <p className="setup-step-copy">
        Enter the setup code from the card that came with your Obsidian box. It proves it's you setting this up.
      </p>
      <div className="setup-field">
        <label htmlFor="setup-token">Setup code</label>
        <input
          id="setup-token"
          type="text"
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="XXXX-XXXX-XXXX"
        />
      </div>
      <p className="setup-step-copy setup-token-aside">
        Running Obsidian on your own server? The code is in its log when it starts.
      </p>
      <div className="setup-actions">
        <button className="setup-button primary" disabled={!token.trim()} onClick={() => onContinue(token.trim())}>
          Continue
        </button>
      </div>
    </>
  )
}
