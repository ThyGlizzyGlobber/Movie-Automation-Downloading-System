import Icon from './Icon'
import './StateMessage.css'

export default function ErrorState({ message, retryHref = '#/home' }: { message?: string; retryHref?: string }) {
  return (
    <div className="state-card err">
      <span className="state-circ">
        <Icon name="alert" />
      </span>
      <h4>Couldn't load that</h4>
      {message && <p>{message}</p>}
      <a className="retry" href={retryHref} onClick={() => window.location.reload()}>
        <Icon name="refresh" />
        Try again
      </a>
    </div>
  )
}
