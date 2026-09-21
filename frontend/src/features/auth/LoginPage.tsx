import { useEndUserLogin } from './useEndUserLogin'
import './LoginPage.css'
import AmbientGlow from '../../components/AmbientGlow'
import BootSkeleton from '../../components/BootSkeleton'

export default function LoginPage() {
  const { authUrl, starting, pending, probing, authenticated, error, begin } = useEndUserLogin()
  // A sign-in this page picked back up rather than started: there is no
  // Plex tab of ours to point at, and the person may well have given up
  // on it, so the button stays live to start over rather than locking
  // them out until the PIN times out fifteen minutes from now.
  const resumed = pending && authUrl === null

  // Landing back here from plex.tv is a page load like any other, and
  // for the moment it takes to claim the finished sign-in this is a
  // signed-out app. Keep the boot skeleton up rather than showing a
  // sign-in screen to someone who has just been through it.
  if (probing || authenticated) return <BootSkeleton />

  return (
    <div className="login-page">
      <AmbientGlow posterPath={null} />
      <div className="login-card">
        <img className="login-brand" src="/brand-icon.svg" alt="" />
        <h1 className="login-title">Obsidian</h1>
        <p className="login-copy">
          Sign in with your Plex account.
        </p>
        <button className="login-button" onClick={begin} disabled={starting || (pending && !resumed)}>
          {pending && !resumed ? 'Taking you to Plex…' : 'Sign in with Plex'}
        </button>
        {resumed && (
          <p className="login-hint">
            Finishing the sign-in you started at Plex — this page updates by itself. Sign in again to start over.
          </p>
        )}
        {error && <p className="login-error">{error}</p>}
      </div>
    </div>
  )
}
