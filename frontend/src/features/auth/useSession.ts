import { useQuery } from '@tanstack/react-query'
import { getSession } from '../../api/auth'
import { ApiError } from '../../api/client'
import { FALLBACK_REGION } from '../../lib/regions'

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

// Which country's age ratings to show. It rides down with the session
// (see api.py's get_current_session) rather than having a call of its
// own: nothing can render a rating without it, so a separate request
// would sit in front of the first thing anyone sees.
export function useCertificationRegion(): string {
  return useSession().data?.certification_region || FALLBACK_REGION
}
