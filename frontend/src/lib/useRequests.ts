import { useQuery } from '@tanstack/react-query'
import { listRequests } from '../api/requests'
import { NON_TERMINAL } from './status'

// Every request, for any screen that shows request state. Reads the one
// ['requests'] query and never polls on its own: an interval set per
// component starts a timer per component, and those don't line up.
// AppShell keeps the list fresh with usePollRequests instead.
export function useRequests() {
  return useQuery({ queryKey: ['requests'], queryFn: () => listRequests() })
}

// Mounted once, in AppShell. Fast while something is in flight so
// progress moves; slow otherwise, when the only news would be a request
// someone else just made.
export function usePollRequests() {
  useQuery({
    queryKey: ['requests'],
    queryFn: () => listRequests(),
    refetchInterval: (q) => (q.state.data?.some((r) => NON_TERMINAL.has(r.status)) ? 5000 : 30_000),
  })
}

// Live count of requests still in flight, for the Requests badge in the
// top bar and the tab bar.
export function useActiveRequestCount(): number {
  const { data } = useRequests()
  return data?.filter((r) => NON_TERMINAL.has(r.status)).length ?? 0
}

export function badgeLabel(count: number): string {
  return count > 99 ? '99+' : String(count)
}
