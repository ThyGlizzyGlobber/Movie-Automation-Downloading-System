// Bytes as the nearest sensible unit, one decimal — 62.4 GB, 1.2 TB.
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let value = bytes
  let i = 0
  while (value >= 1000 && i < units.length - 1) {
    value /= 1000
    i++
  }
  return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`
}

export function plexWebUrl(machineId: string | null | undefined, ratingKey: string): string {
  const key = encodeURIComponent(`/library/metadata/${ratingKey}`)
  return machineId ? `https://app.plex.tv/desktop/#!/server/${machineId}/details?key=${key}` : 'https://app.plex.tv/desktop'
}

// "just now", "4m ago", "2h ago", "3d ago" — for "last checked" and
// "updated" lines.
export function relativeTime(iso: string | null): string {
  if (!iso) return 'never'
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}
