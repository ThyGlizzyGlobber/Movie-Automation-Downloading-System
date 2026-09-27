import Icon from '../ui/Icon'
import { statusMeta } from '../../lib/status'

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
    <span className={`poster-chip rq-chip${status === 'searching' ? ' rq-chip-live' : ''}`} style={{ color: meta.tone }}>
      <Icon name={meta.icon} />
      {meta.label}
    </span>
  )
}
