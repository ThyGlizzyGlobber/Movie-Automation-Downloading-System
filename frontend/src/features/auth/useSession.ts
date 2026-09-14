import { useQuery } from '@tanstack/react-query'
import { getSession } from '../../api/auth'
import { ApiError } from '../../api/client'

// `enabled` lets callers hold this off until it's actually meaningful
// (e.g. only once setup is confirmed complete — see App.tsx) rather than
// firing a doomed request against a backend that isn't ready to answer it
// yet.
export function useSession(enabled = true) {
  return useQuery({
    queryKey: ['session'],
    queryFn: getSession,
    enabled,
    retry: false, // a 401 here is an expected, common outcome — not a transient failure to retry
  })
}

export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
