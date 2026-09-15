import type { IconName } from '../components/Icon'

export type RequestStatus =
  | 'queued'
  | 'searching'
  | 'downloading'
  | 'complete'
  | 'no qualifying results'
  | 'insufficient free space'
  | 'failed'
  | 'cancelled'

interface StatusMeta {
  label: string
  cls: string
  /* Icon drawn inside the pill — one per state so the state reads even
     before the color does. */
  icon: IconName
}

const STATUS_META: Record<string, StatusMeta> = {
  queued: { label: 'Queued', cls: 'status-queued', icon: 'clock' },
  searching: { label: 'Searching', cls: 'status-searching', icon: 'loader' },
  downloading: { label: 'Downloading', cls: 'status-downloading', icon: 'download' },
  complete: { label: 'In Plex', cls: 'status-complete', icon: 'check-circle' },
  'downloaded, not filed': { label: 'Importing', cls: 'status-complete', icon: 'plex' },
  'no qualifying results': { label: 'No match', cls: 'status-nomatch', icon: 'info' },
  'insufficient free space': { label: 'No space', cls: 'status-nospace', icon: 'alert' },
  failed: { label: 'Failed', cls: 'status-failed', icon: 'alert-circle' },
  cancelled: { label: 'Cancelled', cls: 'status-queued', icon: 'block' },
}

export const NON_TERMINAL = new Set(['queued', 'searching', 'downloading'])
export const CANCELLABLE = new Set(['queued', 'downloading', 'complete'])

export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status] || { label: status, cls: 'status-queued', icon: 'clock' }
}

// One plain line under the pill on the Requests page: what the state
// means for this title right now.
export function statusDetail(status: string, progress: number | null): string {
  switch (status) {
    case 'downloading':
      return progress != null ? `${Math.round(progress * 100)}% downloaded` : 'Downloading'
    case 'queued':
      return 'Waiting its turn'
    case 'searching':
      return 'Looking for a copy'
    case 'complete':
      return 'Added to Plex'
    case 'downloaded, not filed':
      return 'Downloaded, being filed'
    case 'no qualifying results':
      return 'Nothing matched yet'
    case 'insufficient free space':
      return 'Not enough room on the drive'
    case 'failed':
      // The raw error is technical; the row shows a plain line and keeps
      // the detail in a tooltip (see RequestsPage).
      return 'Something went wrong'
    case 'cancelled':
      return 'Cancelled'
    default:
      return ''
  }
}
