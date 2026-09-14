import { useState } from 'react'
import { postDeploy } from '../../api/admin'
import { ApiError } from '../../api/client'

export default function UpdatesPanel() {
  const [deploying, setDeploying] = useState(false)

  async function checkForUpdates() {
    if (!confirm('Pull the latest deployed code from git now?')) return
    setDeploying(true)
    try {
      const result = await postDeploy()
      alert(`Deployed: ${result.detail}\nNow at ${result.commit}`)
    } catch (err) {
      alert(`Deploy failed: ${err instanceof ApiError ? err.message : 'Something went wrong.'}`)
    } finally {
      setDeploying(false)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Update</h2>
      <button className="settings-btn" disabled={deploying} onClick={checkForUpdates}>
        {deploying ? 'Deploying…' : 'Check for updates'}
      </button>
      <div className="about-footer">Smithflix</div>
    </div>
  )
}
