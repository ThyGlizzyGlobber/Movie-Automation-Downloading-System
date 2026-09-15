import { useState } from 'react'
import { postDeploy } from '../../api/admin'
import { ApiError } from '../../api/client'

export default function UpdatesPanel() {
  const [deploying, setDeploying] = useState(false)

  async function checkForUpdates() {
    if (!confirm('Install the latest version of Meridian now?')) return
    setDeploying(true)
    try {
      const result = await postDeploy()
      alert(`Updated. ${result.detail}\nVersion ${result.commit}`)
    } catch (err) {
      alert(`Update failed. ${err instanceof ApiError ? err.message : 'Something went wrong.'}`)
    } finally {
      setDeploying(false)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Updates</h2>
      <p className="settings-sub">Install the latest version of Meridian.</p>
      <button className="settings-btn" disabled={deploying} onClick={checkForUpdates}>
        {deploying ? 'Updating…' : 'Check for updates'}
      </button>
      <div className="about-footer">Meridian</div>
    </div>
  )
}
