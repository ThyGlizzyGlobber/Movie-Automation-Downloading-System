import './StateMessage.css'

export default function ErrorState({
  message,
  retryHref = '#/home',
}: {
  message?: string
  retryHref?: string
}) {
  return (
    <div className="error">
      Couldn't load that.
      <br />
      {message}
      <br />
      <a className="retry" href={retryHref} onClick={() => window.location.reload()}>
        Retry
      </a>
    </div>
  )
}
