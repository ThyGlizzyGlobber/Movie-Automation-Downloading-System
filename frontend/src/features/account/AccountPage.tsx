import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useSession } from '../auth/useSession'
import { logout } from '../../api/auth'
import { usePageTitle } from '../../lib/chrome'
import './AccountPage.css'

// Deliberately small — this app has almost no per-user preferences to
// hold (dark-only theme, no notifications system, the request queue is
// shared), so this covers exactly: who's signed in, sign out, and
// replaying the tutorial on demand. Distinct from admin-only /settings —
// every authenticated user reaches this one (Part I).
export default function AccountPage() {
  usePageTitle('Account')
  const session = useSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [loggingOut, setLoggingOut] = useState(false)

  async function handleLogout() {
    setLoggingOut(true)
    try {
      await logout()
    } finally {
      queryClient.invalidateQueries({ queryKey: ['session'] })
    }
  }

  function replayTutorial() {
    // A simple client-side re-trigger: the tutorial only hides itself
    // once has_seen_tutorial is true, so locally treating the session as
    // "not yet seen" (without touching the server flag) shows it again
    // without affecting whether it auto-shows for this account elsewhere.
    queryClient.setQueryData(['session'], (prev: typeof session.data) =>
      prev ? { ...prev, has_seen_tutorial: false } : prev,
    )
    navigate('/home')
  }

  if (!session.data) return null

  return (
    <div className="account-page">
      <div className="account-card">
        <span className="material-symbols-rounded account-avatar">account_circle</span>
        <h1 className="account-username">{session.data.username}</h1>
        {session.data.is_admin && <p className="account-role">Admin</p>}
        <div className="account-actions">
          <button className="account-button secondary" onClick={replayTutorial}>
            Replay tutorial
          </button>
          <button className="account-button danger" onClick={handleLogout} disabled={loggingOut}>
            {loggingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </div>
    </div>
  )
}
