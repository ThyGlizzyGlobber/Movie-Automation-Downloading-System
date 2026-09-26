import Icon from './Icon'
import { statusMeta } from '../lib/status'

// The chip's colour per state, as on the home page's "On the way" row.
const STATUS_TONE: Record<string, string> = {
  queued: 'var(--text)',
  searching: 'var(--status-searching)',
  failed: 'var(--status-failed)',
  cancelled: 'var(--text-dim)',
  'downloaded, not filed': 'var(--status-nomatch)',
  'no qualifying results': 'var(--status-nomatch)',
  'insufficient free space': 'var(--status-nospace)',
}

// Top-right of a poster in the Requests and Following grids: what state
// the title's request is in, unless it's downloading — the bar under the
// poster says that on its own. `completeLabel` renames the finished
// state where "On Plex" would overclaim (Following: the latest episode
// arrived, not necessarily the whole show).
export default function RequestStatusChip({ status, completeLabel = 'On Plex' }: { status: string; completeLabel?: string }) {
  if (status === 'downloading') return null
  if (status === 'complete') return <div className="on-plex-badge">{completeLabel}</div>
  const meta = statusMeta(status)
  return (
    <span className={`poster-chip rq-chip${status === 'searching' ? ' rq-chip-live' : ''}`} style={{ color: STATUS_TONE[status] ?? 'var(--text-dim)' }}>
      <Icon name={meta.icon} />
      {meta.label}
    </span>
  )
}
