import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { getLoginStatus, startLogin } from '../../api/auth'

const POLL_INTERVAL_MS = 2500
// A poll that gives up on its first failed request strands the user on
// "Waiting for Plex…" while the backend behind it goes right on
// resolving the PIN — the failure everyone hits is the slow first
// sign-in (typing credentials, fetching a 2FA code) outlasting whatever
// blip happens to land. So a transport failure (429, proxy hiccup, wifi
// dropping mid-sign-in) is retried rather than surfaced, and only the
// backend's own verdict in `status.error` stops the loop. The cap is
// what keeps "retry" from meaning "forever" when the backend is simply
// gone; the backend's PIN timeout supplies the terminal error in the
// ordinary case, so this only has to catch the unreachable one.
const MAX_CONSECUTIVE_FAILURES = 8
// Ease off while failures are consecutive, so a limit that *is* being
// hit gets a chance to drain instead of being hammered flat.
const MAX_BACKOFF_MULTIPLIER = 4

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
  // Bumped on every stop, so a tick whose request was already in flight
  // when we stopped can tell it's stale and bail instead of scheduling
  // itself again past an unmount or a restarted attempt.
  const runRef = useRef(0)
  const queryClient = useQueryClient()

  const stopPolling = useCallback(() => {
    runRef.current += 1
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
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
      const run = runRef.current

      let failures = 0
      const tick = async () => {
        try {
          const status = await getLoginStatus()
          if (runRef.current !== run) return
          failures = 0
          if (status.error) {
            // The backend has an actual verdict (no access to this
            // server, PIN expired, account unverifiable) — terminal,
            // and retrying it would only repeat the same answer.
            setError(status.error)
            return
          }
          if (status.authenticated) {
            setAuthenticated(true)
            // The session cookie is now set — every other query that
            // depends on being signed in should refetch.
            queryClient.invalidateQueries({ queryKey: ['session'] })
            return
          }
          if (!status.pending) {
            // Not finished, not failed, and the backend isn't working on
            // it either — which it only ever says when it can't match
            // this browser to a live attempt: the attempt cookie didn't
            // come back, or the backend forgot the attempt (restarted,
            // expired, evicted). A live attempt always reports pending,
            // so this can't be a lull between polls. Stopping here beats
            // spinning on "Waiting for Plex…" for a sign-in that nothing
            // is going to finish.
            setError('That sign-in attempt expired. Please try again.')
            return
          }
        } catch (err) {
          if (runRef.current !== run) return
          failures += 1
          if (failures >= MAX_CONSECUTIVE_FAILURES) {
            setError(err instanceof Error ? err.message : String(err))
            return
          }
          // Otherwise swallow it: the user stays on "Waiting for Plex…"
          // rather than being shown a scary transient we're about to
          // recover from anyway.
        }
        timerRef.current = window.setTimeout(
          tick,
          POLL_INTERVAL_MS * Math.min(failures + 1, MAX_BACKOFF_MULTIPLIER),
        )
      }
      timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }, [queryClient, stopPolling])

  return { authUrl, starting, error, authenticated, pending: authUrl !== null && !authenticated && !error, begin }
}
