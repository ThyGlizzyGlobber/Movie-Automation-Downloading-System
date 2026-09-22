import { useQuery } from '@tanstack/react-query'
import { getSession } from '../../api/auth'
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
    retry: false, // what's left to fail here is genuine: 401 comes back as data, not an error
  })
}

// No isUnauthenticated() any more: being signed out is `data === null`,
// not an error to interrogate. `undefined` still means the question is
// open — never asked, or the first ask is still in flight — and callers
// have to keep telling those two apart, because rendering a sign-in form
// at someone whose session simply hasn't loaded yet is the same bug in
// the other direction.

// Which country's age ratings to show. It rides down with the session
// (see api.py's get_current_session) rather than having a call of its
// own: nothing can render a rating without it, so a separate request
// would sit in front of the first thing anyone sees.
//
// `false` is what makes that true, and it is load-bearing rather than a
// micro-optimisation. Left enabled, this reads as a passive lookup while
// actually being a second observer on ['session'] — and a second observer
// mounting on a stale query is a *fetch*, by refetchOnMount. That is the
// call this hook exists not to make, and it lands in the one place it
// does real damage: HomeSkeleton renders HeroCarousel, which calls this,
// and BootSkeleton renders HomeSkeleton — so the skeleton shown *because*
// the session is still unknown was itself asking for the session again.
//
// Signed out, that closed a loop with no exit. App renders LoginPage on a
// 401; LoginPage shows BootSkeleton while it probes; the skeleton's
// refetch clears the error (React Query resets status to `pending` when a
// query with no data starts fetching), which flips App back to isLoading
// and unmounts LoginPage mid-probe; the 401 returns, LoginPage mounts
// fresh with `probing` back to true, and renders the same skeleton again.
// About forty requests a second, a sign-in button that can never appear,
// and — once the login rate limit noticed — a 429 that looked like the
// cause rather than the one thing correctly objecting.
//
// Disabled, this reads the cache and nothing else. The session is fetched
// by App's own useSession, which is the only caller that should be
// deciding when to ask.
export function useCertificationRegion(): string {
  return useSession(false).data?.certification_region || FALLBACK_REGION
}
