import ProgressRing from './ProgressRing'
import Icon, { type IconName } from './Icon'
import './StatusRing.css'

// The reference's ring for every request state, not only a live
// download: a dashed idle ring while queued, a spinning arc while
// searching, a full mint ring with a tick once in Plex, and a faded
// coral ring for anything that stopped. Downloading is the real
// ProgressRing with its percentage.
export default function StatusRing({ status, progress, size = 56 }: { status: string; progress: number | null; size?: number }) {
  if (status === 'downloading') return <ProgressRing progress={progress ?? 0} size={size} />
  const stroke = 3.5
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  let cls = 'idle'
  let icon: IconName | null = 'clock'
  let dash: string | undefined
  switch (status) {
    case 'searching':
      cls = 'searching'
      icon = null
      dash = `${c * 0.25} ${c * 0.75}`
      break
    case 'complete':
    case 'downloaded, not filed':
      cls = 'done'
      icon = 'check'
      break
    case 'failed':
      cls = 'failed'
      icon = 'close'
      break
    case 'no qualifying results':
      cls = 'nomatch'
      icon = 'info'
      break
    case 'insufficient free space':
      cls = 'nospace'
      icon = 'alert'
      break
    case 'cancelled':
      cls = 'cancelled'
      icon = 'block'
      break
    default:
      dash = '3 5'
  }
  return (
    <div className={`status-ring ${cls}`} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox={`0 0 ${size} ${size}`}>
        <circle className="track" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} />
        <circle className="fill" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} strokeDasharray={dash} />
      </svg>
      {icon && (
        <span>
          <Icon name={icon} />
        </span>
      )}
    </div>
  )
}
