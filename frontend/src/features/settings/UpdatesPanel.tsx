import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getAbout, postDeploy } from '../../api/admin'
import { ApiError } from '../../api/client'
import { useToast } from '../../lib/toast'

export default function UpdatesPanel() {
  const [deploying, setDeploying] = useState(false)
  const { toast } = useToast()
  // Same query the About panel keeps warm. A release image has no clone to
  // pull, so it gets no pull button: it updates by image instead.
  const about = useQuery({ queryKey: ['about'], queryFn: getAbout, refetchInterval: 60_000 })
  const channel = about.data?.channel

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
      {channel ? (
        <p className="settings-sub">
          This install follows the {channel} release channel
          {about.data?.version ? `, and is running v${about.data.version}` : ''}.
        </p>
      ) : (
        <>
          <p className="settings-sub">Install the latest version of Obsidian.</p>
          <button className="settings-btn" disabled={deploying || about.isLoading} onClick={checkForUpdates}>
            {deploying ? 'Updating…' : 'Check for updates'}
          </button>
        </>
      )}
      <div className="about-footer">Obsidian</div>
    </div>
  )
}
