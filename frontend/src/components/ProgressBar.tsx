import './ProgressBar.css'

// Part J2's shared primitive — a live qBittorrent progress fraction as a
// thin bar. Reused by the Requests queue and (per the migration plan)
// the Watching list.
export default function ProgressBar({ progress }: { progress: number }) {
  const pct = Math.max(0, Math.min(1, progress)) * 100
  return (
    <div className="progress-bar" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
    </div>
  )
}
