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
  /* Material Symbols name drawn inside the pill — one icon per state so
     the state reads even before the color does. */
  icon: string
}

const STATUS_META: Record<string, StatusMeta> = {
  queued: { label: 'Queued', cls: 'status-queued', icon: 'schedule' },
  searching: { label: 'Searching…', cls: 'status-searching', icon: 'progress_activity' },
  downloading: { label: 'Downloading…', cls: 'status-downloading', icon: 'download' },
  complete: { label: 'Ready', cls: 'status-complete', icon: 'check_circle' },
  'no qualifying results': { label: 'No matches found', cls: 'status-nomatch', icon: 'info' },
  'insufficient free space': { label: 'Not enough space', cls: 'status-nospace', icon: 'warning' },
  failed: { label: 'Failed', cls: 'status-failed', icon: 'error' },
  cancelled: { label: 'Cancelled', cls: 'status-queued', icon: 'block' },
}

export const NON_TERMINAL = new Set(['queued', 'searching', 'downloading'])
export const CANCELLABLE = new Set(['queued', 'downloading', 'complete'])

export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status] || { label: status, cls: 'status-queued', icon: 'schedule' }
}
