import { useSetupStatus } from './features/setup/useSetupStatus'
import { isUnauthenticated, useSession } from './features/auth/useSession'
import SetupWizard from './features/setup/SetupWizard'
import LoginPage from './features/auth/LoginPage'
import LoadingState from './components/LoadingState'
import ErrorState from './components/ErrorState'
import { logout } from './api/auth'
import { useQueryClient } from '@tanstack/react-query'

function App() {
  const setupStatus = useSetupStatus()
  const setupComplete = setupStatus.data?.setup_complete === true
  const session = useSession(setupComplete)
  const queryClient = useQueryClient()

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

  // Persistent chrome, real routing, and pages land in later steps — this
  // is a temporary placeholder confirming the auth gate itself works end
  // to end.
  return (
    <div style={{ padding: 40 }}>
      <p>
        Signed in as <strong>{session.data.username}</strong>
        {session.data.is_admin && ' (admin)'}.
      </p>
      <button
        onClick={async () => {
          await logout()
          queryClient.invalidateQueries({ queryKey: ['session'] })
        }}
      >
        Log out
      </button>
    </div>
  )
}

export default App
