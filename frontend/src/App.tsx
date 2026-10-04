import { lazy, Suspense } from 'react'
import { RouterProvider } from 'react-router-dom'
import { useSetupStatus } from './features/setup/useSetupStatus'
import { useSession } from './features/auth/useSession'
import BootSkeleton from './app/BootSkeleton'
import ErrorState from './components/ui/ErrorState'
import { router } from './app/routes'

// Seen once per install and once per sign-in, so neither rides in the
// bundle every signed-in visit downloads.
const SetupWizard = lazy(() => import('./features/setup/SetupWizard'))
const LoginPage = lazy(() => import('./features/auth/LoginPage'))

function App() {
  const setupStatus = useSetupStatus()
  const setupComplete = setupStatus.data?.setup_complete === true
  // Asked alongside the setup check, not after it: waiting cost every
  // visit a round trip for the sake of the one before setup, when the
  // answer is simply a 401 (no session can exist yet) and PlexLinkStep
  // asks again once it has signed the admin in.
  const session = useSession()

  if (setupStatus.isLoading) return <BootSkeleton />
  if (setupStatus.isError) {
    return <ErrorState message={setupStatus.error instanceof Error ? setupStatus.error.message : undefined} />
  }
  if (!setupComplete) {
    return (
      <Suspense fallback={<BootSkeleton />}>
        <SetupWizard />
      </Suspense>
    )
  }

  // Three states, told apart by the value rather than by the query's
  // status: `undefined` is the question still open (never asked, or the
  // first ask still in flight), `null` is asked and answered — nobody is
  // signed in — and an object is a session. Reading `isLoading` here
  // instead is what made this loop: a refetch of a query with no data
  // reports `pending` again, so every re-ask of an already-known 401
  // looked like a first load and tore LoginPage down mid-sign-in.
  if (session.isError) {
    return <ErrorState message={session.error instanceof Error ? session.error.message : undefined} />
  }
  if (session.data === undefined) return <BootSkeleton />
  if (session.data === null) {
    return (
      <Suspense fallback={<BootSkeleton />}>
        <LoginPage />
      </Suspense>
    )
  }

  return <RouterProvider router={router} />
}

export default App
