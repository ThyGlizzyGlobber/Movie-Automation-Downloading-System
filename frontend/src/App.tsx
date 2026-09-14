import { RouterProvider } from 'react-router-dom'
import { useSetupStatus } from './features/setup/useSetupStatus'
import { isUnauthenticated, useSession } from './features/auth/useSession'
import SetupWizard from './features/setup/SetupWizard'
import LoginPage from './features/auth/LoginPage'
import LoadingState from './components/LoadingState'
import ErrorState from './components/ErrorState'
import { router } from './router/routes'

function App() {
  const setupStatus = useSetupStatus()
  const setupComplete = setupStatus.data?.setup_complete === true
  const session = useSession(setupComplete)

  if (setupStatus.isLoading) return <LoadingState />
  if (setupStatus.isError) {
    return <ErrorState message={setupStatus.error instanceof Error ? setupStatus.error.message : undefined} />
  }
  if (!setupComplete) return <SetupWizard />

  if (session.isLoading) return <LoadingState />
  if (session.isError) {
    if (isUnauthenticated(session.error)) return <LoginPage />
    return <ErrorState message={session.error instanceof Error ? session.error.message : undefined} />
  }
  if (!session.data) return <LoadingState />

  return <RouterProvider router={router} />
}

export default App
