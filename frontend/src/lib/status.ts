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
  | 'downloaded, not filed'

interface StatusMeta {
  label: string
  cls: string
  /* Icon drawn inside the pill — one per state so the state reads even
     before the color does. */
  icon: IconName
}

const STATUS_META: Record<string, StatusMeta> = {
  queued: { label: 'Queued', cls: 'status-queued', icon: 'clock' },
  searching: { label: 'Searching', cls: 'status-searching', icon: 'scan' },
  downloading: { label: 'Downloading', cls: 'status-downloading', icon: 'download' },
  complete: { label: 'On Plex', cls: 'status-complete', icon: 'check-circle' },
  // Not a step on the way to "On Plex", however much the name sounds
  // like one: the backend lists this with the failures, nothing retries
  // it on its own, and it sat here as a green "Importing" pill while a
  // season that would never arrive looked like it was almost there.
  'downloaded, not filed': { label: 'Not filed', cls: 'status-nomatch', icon: 'alert' },
  'no qualifying results': { label: 'No match', cls: 'status-nomatch', icon: 'info' },
  'insufficient free space': { label: 'No space', cls: 'status-nospace', icon: 'alert' },
  failed: { label: 'Failed', cls: 'status-failed', icon: 'alert-circle' },
  cancelled: { label: 'Cancelled', cls: 'status-queued', icon: 'block' },
}

export const NON_TERMINAL = new Set(['queued', 'searching', 'downloading'])
export const CANCELLABLE = new Set(['queued', 'downloading', 'complete'])
// The states the Requests page files under Failed.
export const FAILED_STATES = new Set(['failed', 'no qualifying results', 'insufficient free space'])

export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status] || { label: status, cls: 'status-queued', icon: 'clock' }
}

// One plain line under the pill on the Requests page: what the state
// means for this title right now.
export function statusDetail(status: string, progress: number | null, errorMessage?: string | null): string {
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
      // The download itself worked — the bytes are on the drive — so
      // the line says what's actually true and what it needs, rather
      // than implying either progress or a lost download. The organizer
      // leaves its reason here the same way the no-match case does.
      return errorMessage ? `Downloaded, but couldn't be filed: ${errorMessage}` : "Downloaded, but couldn't be filed"
    case 'no qualifying results':
      // The worker leaves a plain-English reason when the title was found
      // but only under the quality floor ("Found 9 copies, but none at
      // 2160p or better…") — that's the line that tells someone what to do.
      return errorMessage || 'Nothing matched yet'
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
