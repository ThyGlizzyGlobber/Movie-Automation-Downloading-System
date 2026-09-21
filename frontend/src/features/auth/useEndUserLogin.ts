import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { getLoginStatus, startLogin } from '../../api/auth'
import type { LoginStatusResponse } from '../../types/auth'

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
//
// The sign-in itself lives on the far side of this page, not inside it:
// the browser holds the attempt cookie, the backend holds a task
// resolving the PIN, and a session is minted the moment *someone* asks
// /api/auth/login/status. That used to be this page's polling loop and
// nothing else, which made the whole flow depend on the exact page
// instance that pressed the button still being alive, awake and
// unthrottled when the user came back from plex.tv — three assumptions
// a real browser breaks constantly, every one of them silently. So
// asking is now driven from three places: the loop below, a probe on
// mount (the page was reloaded or discarded while we were away), and a
// probe whenever the tab is shown again (the page survived but its
// timers were throttled to a crawl behind the Plex tab).
export function useEndUserLogin() {
  const [authUrl, setAuthUrl] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [authenticated, setAuthenticated] = useState(false)
  // Whether a sign-in is in flight as far as this page is concerned —
  // one it started, or one it adopted. Deliberately not derived from
  // `authUrl` the way it used to be: an adopted attempt was started by a
  // previous life of this page and has no auth_url to show for it.
  const [waiting, setWaiting] = useState(false)
  // True until the mount probe below has had its answer. The page holds
  // its skeleton until then rather than flashing a "Sign in with Plex"
  // button at someone who has, in fact, just signed in with Plex.
  const [probing, setProbing] = useState(true)
  const timerRef = useRef<number | null>(null)
  // Bumped on every stop, so a tick whose request was already in flight
  // when we stopped can tell it's stale and bail instead of scheduling
  // itself again past an unmount or a restarted attempt.
  const runRef = useRef(0)
  // What the probes need to know without waiting for a re-render: is a
  // sign-in already being polled for, and has this page already reached
  // a verdict it shouldn't reopen?
  const waitingRef = useRef(false)
  const settledRef = useRef(false)
  const queryClient = useQueryClient()

  const stopPolling = useCallback(() => {
    runRef.current += 1
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const setWaitingBoth = useCallback((value: boolean) => {
    waitingRef.current = value
    setWaiting(value)
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  // Applies whatever the backend just said about the attempt. True once
  // it has an answer either way and there is nothing left to poll for.
  const settle = useCallback(
    (status: LoginStatusResponse): boolean => {
      if (status.error) {
        // The backend has an actual verdict (no access to this server,
        // PIN expired, account unverifiable) — terminal, and retrying it
        // would only repeat the same answer.
        settledRef.current = true
        setError(status.error)
        setWaitingBoth(false)
        return true
      }
      if (status.authenticated) {
        settledRef.current = true
        setAuthenticated(true)
        // The session cookie is now set — every other query that
        // depends on being signed in should refetch.
        queryClient.invalidateQueries({ queryKey: ['session'] })
        return true
      }
      return false
    },
    [queryClient, setWaitingBoth],
  )

  const startPolling = useCallback(
    (immediate = false) => {
      stopPolling()
      const run = runRef.current

      let failures = 0
      const tick = async () => {
        try {
          const status = await getLoginStatus()
          if (runRef.current !== run) return
          failures = 0
          if (settle(status)) return
          if (!status.pending) {
            // Not finished, not failed, and the backend isn't working on
            // it either — which it only ever says when it can't match
            // this browser to a live attempt: the attempt cookie didn't
            // come back, or the backend forgot the attempt (restarted,
            // expired, evicted). A live attempt always reports pending,
            // so this can't be a lull between polls. Stopping here beats
            // spinning on "Waiting for Plex…" for a sign-in that nothing
            // is going to finish.
            settledRef.current = true
            setError('That sign-in attempt expired. Please try again.')
            setWaitingBoth(false)
            return
          }
        } catch (err) {
          if (runRef.current !== run) return
          failures += 1
          if (failures >= MAX_CONSECUTIVE_FAILURES) {
            settledRef.current = true
            setError(err instanceof Error ? err.message : String(err))
            setWaitingBoth(false)
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
      timerRef.current = window.setTimeout(tick, immediate ? 0 : POLL_INTERVAL_MS)
    },
    [settle, setWaitingBoth, stopPolling],
  )

  // Ask the backend whether this browser already has a sign-in worth
  // knowing about, and adopt it if so. Safe to call on a browser that
  // has never signed in: with no attempt cookie the backend reports
  // nothing pending, which is the ordinary case and says nothing worth
  // showing anyone.
  const probe = useCallback(async () => {
    if (waitingRef.current || settledRef.current) return
    try {
      const status = await getLoginStatus()
      // A click that got in first owns the flow; leave it alone rather
      // than restarting its polling underneath it.
      if (waitingRef.current || settledRef.current) return
      if (settle(status)) return
      if (status.pending) {
        setWaitingBoth(true)
        startPolling()
      }
    } catch {
      // Nothing has been attempted from this page yet, so there is
      // nothing to report a failure about; the sign-in button is right
      // there either way, and pressing it surfaces a real backend
      // problem properly.
    }
  }, [settle, setWaitingBoth, startPolling])

  // Reloaded, restored from a discarded tab, or opened fresh from a
  // bookmark — all of which land here with no memory of a sign-in that
  // may well have finished while this page didn't exist. The attempt
  // cookie outlives the page (15 minutes) and so does the backend's
  // task, so the answer is one request away.
  useEffect(() => {
    void probe().finally(() => setProbing(false))
  }, [probe])

  // Coming back from the Plex tab. Chrome throttles timers in a hidden
  // tab hard — to about once a minute once it's been in the background
  // a few minutes, which is exactly how long a first Plex sign-in takes
  // — so the loop below may be mid-nap at the moment the user returns
  // and starts wondering why nothing is happening. Ask immediately
  // instead of waiting the throttled timer out.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible' || settledRef.current) return
      if (waitingRef.current) startPolling(true)
      else void probe()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [probe, startPolling])

  const begin = useCallback(async () => {
    settledRef.current = false
    setError(null)
    setAuthenticated(false)
    setStarting(true)
    // Claimed before the request goes out, so a probe landing in the
    // meantime doesn't mistake this page for an idle one and adopt some
    // older attempt out from under the click.
    waitingRef.current = true
    try {
      const { auth_url } = await startLogin()
      setAuthUrl(auth_url)
      setWaitingBoth(true)
      // Same tab, not a popup. This is the ordinary shape of an OAuth
      // sign-in — hand the browser to the provider, let the provider
      // hand it back — and it removes the two things that used to break
      // this one: a popup blocked because `window.open` no longer counts
      // as user-initiated once there's an `await` in front of it, and a
      // background tab that has to survive the entire trip for the
      // sign-in to be claimed. Coming back is now a page load, and a
      // page load asks (see `probe`).
      window.location.assign(auth_url)
      // Belt and braces for a navigation that doesn't take (or a quick
      // press of Back): this loop dies with the page in the normal case.
      startPolling()
    } catch (err) {
      settledRef.current = true
      setError(err instanceof Error ? err.message : String(err))
      setWaitingBoth(false)
    } finally {
      setStarting(false)
    }
  }, [setWaitingBoth, startPolling])

  return {
    authUrl,
    starting,
    error,
    authenticated,
    probing,
    pending: waiting && !authenticated && !error,
    begin,
  }
}
