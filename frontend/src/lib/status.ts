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
}

const STATUS_META: Record<string, StatusMeta> = {
  queued: { label: 'Queued', cls: 'status-queued' },
  searching: { label: 'Searching…', cls: 'status-searching' },
  downloading: { label: 'Downloading…', cls: 'status-downloading' },
  complete: { label: 'Ready', cls: 'status-complete' },
  'no qualifying results': { label: 'No matches found', cls: 'status-nomatch' },
  'insufficient free space': { label: 'Not enough space', cls: 'status-nospace' },
  failed: { label: 'Failed', cls: 'status-failed' },
  cancelled: { label: 'Cancelled', cls: 'status-queued' },
}

export const NON_TERMINAL = new Set(['queued', 'searching', 'downloading'])
export const CANCELLABLE = new Set(['queued', 'downloading', 'complete'])

export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status] || { label: status, cls: 'status-queued' }
}
