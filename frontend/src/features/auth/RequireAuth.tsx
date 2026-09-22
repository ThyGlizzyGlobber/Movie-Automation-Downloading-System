import type { ReactNode } from 'react'
import { useSession } from './useSession'
import LoginPage from './LoginPage'

// The top-level gate in App.tsx already renders LoginPage/SetupWizard
// instead of the router at all when there's no session, so this mainly
// exists for a route that might one day be reachable before that gate
// runs (e.g. a future public route mixed into the same router). Safe to
// use defensively; redundant, not harmful, everywhere else.
export default function RequireAuth({ children }: { children: ReactNode }) {
  const session = useSession()
  if (session.isError) throw session.error
  if (session.data === undefined) return null
  if (session.data === null) return <LoginPage />
  return <>{children}</>
}
