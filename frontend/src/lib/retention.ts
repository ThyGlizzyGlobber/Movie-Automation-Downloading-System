// Shared by the Requests queue's auto-clear picker and Settings' own
// Retention section — one source of truth for the option list/labels.
export const RETENTION_OPTIONS: { days: number | null; label: string }[] = [
  { days: null, label: 'Keep forever' },
  { days: 30, label: 'Clear after 30 days' },
  { days: 60, label: 'Clear after 60 days' },
  { days: 90, label: 'Clear after 90 days' },
  { days: 180, label: 'Clear after 180 days' },
  { days: 365, label: 'Clear after 1 year' },
]
