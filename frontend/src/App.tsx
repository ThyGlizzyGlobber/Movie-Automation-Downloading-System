import { RouterProvider } from 'react-router-dom'
import { useSetupStatus } from './features/setup/useSetupStatus'
import { useSession } from './features/auth/useSession'
import SetupWizard from './features/setup/SetupWizard'
import LoginPage from './features/auth/LoginPage'
import BootSkeleton from './components/BootSkeleton'
import ErrorState from './components/ErrorState'
import { router } from './router/routes'

function App() {
  const setupStatus = useSetupStatus()
  const setupComplete = setupStatus.data?.setup_complete === true
  const session = useSession(setupComplete)

  if (setupStatus.isLoading) return <BootSkeleton />
  if (setupStatus.isError) {
    return <ErrorState message={setupStatus.error instanceof Error ? setupStatus.error.message : undefined} />
  }
  if (!setupComplete) return <SetupWizard />

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
  if (session.data === null) return <LoginPage />

  return <RouterProvider router={router} />
}

export default App
