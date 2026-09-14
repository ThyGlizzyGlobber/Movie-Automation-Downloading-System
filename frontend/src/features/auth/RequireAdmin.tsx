import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useSession } from './useSession'
import LoadingState from '../../components/LoadingState'

// The top-level gate in App.tsx already guarantees a real session exists
// before the router ever mounts — this only narrows further, to admin.
export default function RequireAdmin({ children }: { children: ReactNode }) {
  const session = useSession()
  if (session.isLoading || !session.data) return <LoadingState />
  if (!session.data.is_admin) return <Navigate to="/home" replace />
  return <>{children}</>
}
