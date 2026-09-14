import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { getLoginStatus, startLogin } from '../../api/auth'

const POLL_INTERVAL_MS = 2500

// Drives the end-user Plex PIN sign-in flow: click "Sign in with Plex" ->
// open app.plex.tv in a new tab -> poll until Plex resolves the PIN and
// the backend confirms server access. Deliberately not shared with the
// setup wizard's admin server-linking flow (PlexLinkStep) — the two use
// different endpoints/status shapes, and forcing one generic abstraction
// over both wasn't worth it for two call sites.
export function useEndUserLogin() {
  const [authUrl, setAuthUrl] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [authenticated, setAuthenticated] = useState(false)
  const timerRef = useRef<number | null>(null)
  const queryClient = useQueryClient()

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  const begin = useCallback(async () => {
    setError(null)
    setAuthenticated(false)
    setStarting(true)
    try {
      const { auth_url } = await startLogin()
      setAuthUrl(auth_url)
      window.open(auth_url, '_blank', 'noopener,noreferrer')
      stopPolling()
      timerRef.current = window.setInterval(async () => {
        try {
          const status = await getLoginStatus()
          if (status.error) {
            setError(status.error)
            stopPolling()
            return
          }
          if (status.authenticated) {
            setAuthenticated(true)
            stopPolling()
            // The session cookie is now set — every other query that
            // depends on being signed in should refetch.
            queryClient.invalidateQueries({ queryKey: ['session'] })
          }
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err))
          stopPolling()
        }
      }, POLL_INTERVAL_MS)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }, [queryClient, stopPolling])

  return { authUrl, starting, error, authenticated, pending: authUrl !== null && !authenticated && !error, begin }
}
