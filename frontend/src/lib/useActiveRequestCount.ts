import { useQuery } from '@tanstack/react-query'
import { listRequests } from '../api/requests'
import { NON_TERMINAL } from './status'

// Live count of requests still in flight, for the Requests badge in the
// top bar and the tab bar. Shares the Requests page's own ['requests']
// query key so the app polls once, not once per badge.
export function useActiveRequestCount(): number {
  const { data } = useQuery({
    queryKey: ['requests'],
    queryFn: () => listRequests(),
    refetchInterval: 5000,
  })
  return data?.filter((r) => NON_TERMINAL.has(r.status)).length ?? 0
}

// Requests that finished today — lights the bell in the top bar.
export function useCompletedTodayCount(): number {
  const { data } = useQuery({
    queryKey: ['requests'],
    queryFn: () => listRequests(),
    refetchInterval: 5000,
  })
  const now = new Date()
  return (
    data?.filter((r) => {
      if (r.status !== 'complete') return false
      const d = new Date(r.updated_at)
      return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
    }).length ?? 0
  )
}

export function badgeLabel(count: number): string {
  return count > 99 ? '99+' : String(count)
}

// Up to two initials from a Plex username, for the avatar circle.
export function initialsOf(username: string | null | undefined): string {
  if (!username) return ''
  const parts = username
    .replace(/[_.-]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  const word = parts[0] ?? ''
  // A single camel-cased handle ("BejaySmith") still yields two letters.
  const camel = word.match(/[A-Z]/g)
  if (camel && camel.length >= 2) return (camel[0] + camel[1]).toUpperCase()
  return word.slice(0, 2).toUpperCase()
}
