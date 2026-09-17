import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useSession } from './useSession'

// The top-level gate in App.tsx already guarantees a real session exists
// before the router ever mounts — this only narrows further, to admin.
export default function RequireAdmin({ children }: { children: ReactNode }) {
  const session = useSession()
  // Already loaded by App.tsx's gate; nothing to show in between.
  if (session.isLoading || !session.data) return null
  if (!session.data.is_admin) return <Navigate to="/home" replace />
  return <>{children}</>
}
