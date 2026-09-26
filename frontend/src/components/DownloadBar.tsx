import './DownloadBar.css'

// A live download as a thin glowing bar with its percentage at the end,
// the Requests grid's treatment under a poster. A sheen runs along the
// filled part so a download that hasn't moved since the last poll still
// reads as alive. Before qBittorrent's first report (progress null) the
// bar sweeps without a fill, since 0% would claim something it doesn't
// know yet.
export default function DownloadBar({ progress, className }: { progress: number | null; className?: string }) {
  const known = progress != null
  const pct = known ? Math.round(Math.max(0, Math.min(1, progress)) * 100) : null
  return (
    <div
      className={`dl-bar${known ? '' : ' dl-bar-pending'}${className ? ` ${className}` : ''}`}
      role="progressbar"
      aria-label="Downloaded"
      aria-valuenow={pct ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span className="dl-bar-track">
        <i style={pct != null ? { width: `${pct}%` } : undefined} />
      </span>
      <b>{pct != null ? `${pct}%` : '…'}</b>
    </div>
  )
}
