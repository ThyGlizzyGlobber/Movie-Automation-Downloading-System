import { ApiError, request } from './client'
import type { LoginStartResponse, LoginStatusResponse, SessionInfo } from '../types/auth'

export function startLogin() {
  return request<LoginStartResponse>('/api/auth/login/start', { method: 'POST' })
}

export function getLoginStatus() {
  return request<LoginStatusResponse>('/api/auth/login/status')
}

// `null` means "asked, and nobody is signed in" — a real answer, not a
// failure to get one. Modelling it as a thrown error made the signed-out
// state the *absence* of an answer, which React Query renders as
// `status: 'pending'` the moment anything refetches (it clears the error
// on a query that has no data to fall back on). Every refetch therefore
// looked like a first load, and App tore the whole signed-out UI down and
// rebuilt it — see the comment in useSession.ts for what that cost.
//
// Deliberately only 401. A 403, a 500, a dropped connection are all still
// thrown: they mean the answer is unknown, which is a different thing from
// knowing there is no session, and the app owes the user an error rather
// than a sign-in form that implies the fix is to sign in. Nothing here
// widens what counts as *signed in* — that still requires the backend to
// have returned a session.
export async function getSession(): Promise<SessionInfo | null> {
  try {
    return await request<SessionInfo>('/api/auth/session')
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null
    throw err
  }
}

export function logout() {
  return request<{ logged_out: true }>('/api/auth/logout', { method: 'POST' })
}

export function markTutorialSeen() {
  return request<{ has_seen_tutorial: true }>('/api/auth/tutorial-seen', { method: 'PUT' })
}
