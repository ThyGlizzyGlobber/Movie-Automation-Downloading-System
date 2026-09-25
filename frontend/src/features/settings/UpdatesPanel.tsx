import { useState } from 'react'
import { postDeploy } from '../../api/admin'
import { ApiError } from '../../api/client'
import { useToast } from '../../lib/toast'

export default function UpdatesPanel() {
  const [deploying, setDeploying] = useState(false)
  const { toast } = useToast()

  async function checkForUpdates() {
    if (!confirm('Install the latest version of Obsidian now?')) return
    setDeploying(true)
    try {
      const result = await postDeploy()
      toast({ tone: 'ok', title: 'Updated', body: `${result.detail} | ${result.commit}` })
    } catch (err) {
      toast({ tone: 'error', title: 'Update failed', body: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setDeploying(false)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Updates</h2>
      <p className="settings-sub">Install the latest version of Obsidian.</p>
      <button className="settings-btn" disabled={deploying} onClick={checkForUpdates}>
        {deploying ? 'Updating…' : 'Check for updates'}
      </button>
      <div className="about-footer">Obsidian</div>
    </div>
  )
}
