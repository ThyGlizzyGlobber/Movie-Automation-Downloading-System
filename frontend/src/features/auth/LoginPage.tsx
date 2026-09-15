import { useEndUserLogin } from './useEndUserLogin'
import './LoginPage.css'
import AmbientGlow from '../../components/AmbientGlow'

export default function LoginPage() {
  const { starting, pending, error, begin } = useEndUserLogin()

  return (
    <div className="login-page">
      <AmbientGlow posterPath={null} />
      <div className="login-card">
        <img className="login-brand" src="/brand-icon.svg" alt="" />
        <h1 className="login-title">Meridian</h1>
        <p className="login-copy">
          Sign in with the Plex account that has access to this household's server.
        </p>
        <button className="login-button" onClick={begin} disabled={starting || pending}>
          {pending ? 'Waiting for Plex…' : 'Sign in with Plex'}
        </button>
        {pending && (
          <p className="login-hint">
            Approve the sign-in in the tab that just opened, then come back here — this updates on its own.
          </p>
        )}
        {error && <p className="login-error">{error}</p>}
      </div>
    </div>
  )
}
