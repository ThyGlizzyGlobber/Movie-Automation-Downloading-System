import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { getPlexStatus, startPlexLink } from '../../api/plex'
import { ApiError } from '../../api/client'
import type { PlexStatus } from '../../types/settings'

const POLL_INTERVAL_MS = 2500
// Same reasoning as useEndUserLogin's own loop, and the same bug it was
// written to kill: this used to stop dead and show "Something went
// wrong" on the *first* failed request, while the backend behind it went
// right on linking the server. The failure everyone actually hits is a
// slow first Plex sign-in — credentials, a 2FA code, switching apps —
// outlasting whatever blip happens to land, so a transport failure is
// retried rather than surfaced and only the backend's own verdict in
// `status.error` stops the loop. The cap keeps "retry" from meaning
// "forever" when the backend is simply gone; the backend's own PIN
// timeout supplies the terminal error in the ordinary case.
const MAX_CONSECUTIVE_FAILURES = 8
// Ease off while failures are consecutive, so a limit that *is* being
// hit gets a chance to drain instead of being hammered flat.
const MAX_BACKOFF_MULTIPLIER = 4

type Phase = 'idle' | 'signing-in' | 'linking'

export default function PlexLinkStep({ setupToken }: { setupToken: string }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  // Kept so a blocked popup isn't a dead end — see the link below.
  const [authUrl, setAuthUrl] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  // Bumped on every stop, so a tick whose request was already in flight
  // when we stopped can tell it's stale and bail instead of scheduling
  // itself again past an unmount or a restarted attempt.
  const runRef = useRef(0)
  // What the probes need to know without waiting for a re-render.
  const phaseRef = useRef<Phase>('idle')
  const queryClient = useQueryClient()

  const stopPolling = useCallback(() => {
    runRef.current += 1
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  const enterPhase = useCallback((next: Phase) => {
    phaseRef.current = next
    setPhase(next)
  }, [])

  // PlexLinker._poll() (backend) already resolves and persists the
  // account's one owned server as soon as the PIN is approved — the same
  // auto-selection PlexServerPanel's own "Sign in with Plex" already
  // relies on (it treats `status.linked && !status.pending` as fully
  // done, no separate picker). This step used to also call
  // GET /api/plex/servers + PUT /api/plex/server here, expecting a
  // distinct "pick a server" step — but by the time that ran,
  // plex_server_machine_id was already set (by _poll() itself), which
  // closes require_admin_or_setup_bootstrap's setup-token window, so
  // that follow-up call 401'd and setup silently finished server-side
  // while this screen sat stuck showing "waiting for approval." Matching
  // PlexServerPanel's own handling avoids the race entirely.
  const complete = useCallback(() => {
    stopPolling()
    enterPhase('linking')
    queryClient.invalidateQueries({ queryKey: ['setupStatus'] })
    queryClient.invalidateQueries({ queryKey: ['session'] })
  }, [enterPhase, queryClient, stopPolling])

  // Applies whatever the backend just said about the link. True once it
  // has an answer either way and there is nothing left to poll for.
  const settle = useCallback(
    (status: PlexStatus): boolean => {
      if (status.error) {
        stopPolling()
        setError(status.error)
        enterPhase('idle')
        return true
      }
      if (status.linked && !status.pending) {
        // `linked` is read straight from persisted settings, and the
        // backend writes the token and the server it belongs to in one
        // go — so this is durable, and true even to a page that wasn't
        // here when it happened.
        complete()
        return true
      }
      return false
    },
    [complete, enterPhase, stopPolling],
  )

  const startPolling = useCallback(
    (immediate = false) => {
      stopPolling()
      const run = runRef.current

      let failures = 0
      const tick = async () => {
        try {
          const status = await getPlexStatus(setupToken)
          if (runRef.current !== run) return
          failures = 0
          if (settle(status)) return
          if (!status.pending) {
            // Nothing linked, nothing failed, and the backend isn't
            // working on it either — the attempt it was polling for is
            // simply gone (the backend restarted, which on this deploy
            // it does on every code change). Nothing is going to finish
            // this one, so say so rather than spin.
            setError('That sign-in attempt expired. Please try again.')
            enterPhase('idle')
            return
          }
        } catch (err) {
          if (runRef.current !== run) return
          failures += 1
          if (failures >= MAX_CONSECUTIVE_FAILURES) {
            setError(err instanceof ApiError ? err.message : 'Something went wrong.')
            enterPhase('idle')
            return
          }
          // Otherwise swallow it: the wizard keeps waiting rather than
          // throwing away a link that is very probably about to land.
        }
        timerRef.current = window.setTimeout(
          tick,
          POLL_INTERVAL_MS * Math.min(failures + 1, MAX_BACKOFF_MULTIPLIER),
        )
      }
      timerRef.current = window.setTimeout(tick, immediate ? 0 : POLL_INTERVAL_MS)
    },
    [enterPhase, settle, setupToken, stopPolling],
  )

  // A link is finished by this page asking, and the trip to plex.tv is
  // long enough that this page may not survive it — a reload, a tab
  // Chrome decided to discard, or an admin who closed the wizard and
  // came back. The backend's linking task outlives all of that, and what
  // it persists outlives even the backend, so the state of play is one
  // request away: ask on mount rather than presenting a "Sign in with
  // Plex" button for a sign-in that is already in flight or already won.
  useEffect(() => {
    let cancelled = false
    getPlexStatus(setupToken)
      .then((status) => {
        // A click that got in first owns the flow; leave it alone.
        if (cancelled || phaseRef.current !== 'idle') return
        if (settle(status)) return
        if (status.pending) {
          enterPhase('signing-in')
          startPolling()
        }
      })
      .catch(() => {
        // Nothing has been attempted from here yet, so there is nothing
        // to report a failure about; the button is right there either
        // way, and pressing it surfaces a real problem properly.
      })
    return () => {
      cancelled = true
    }
  }, [enterPhase, settle, setupToken, startPolling])

  // Coming back from the Plex tab. Chrome throttles timers in a hidden
  // tab hard — to about once a minute once it has been in the background
  // a few minutes, which is roughly how long a first Plex sign-in takes
  // — so the loop above may be mid-nap at the very moment the admin
  // returns and starts wondering why nothing is happening.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      if (phaseRef.current === 'signing-in') startPolling(true)
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [startPolling])

  const begin = useCallback(async () => {
    setError(null)
    enterPhase('signing-in')
    try {
      const { auth_url } = await startPlexLink(setupToken)
      setAuthUrl(auth_url)
      // Deliberately a second tab, not the same-tab redirect the
      // end-user sign-in uses: this wizard holds the setup token and the
      // step you're on in memory only, so navigating away from it would
      // land a cancelled sign-in back at step one with the token to type
      // in again. The recovery above is what makes the popup safe —
      // whatever happens to this tab, the answer is still askable.
      window.open(auth_url, '_blank', 'noopener,noreferrer')
      startPolling()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
      enterPhase('idle')
    }
  }, [enterPhase, setupToken, startPolling])

  return (
    <>
      <h1 className="setup-step-title">Connect Plex</h1>
      <p className="setup-step-copy">Sign in with the Plex account that owns your server. That account becomes the admin.</p>

      {phase === 'idle' && (
        <div className="setup-actions">
          <button className="setup-button primary" onClick={begin}>
            Sign in with Plex
          </button>
        </div>
      )}

      {phase === 'signing-in' && (
        <>
          <p className="setup-step-copy">
            Finish signing in in the tab that opened. This page updates by itself.
          </p>
          {/* `window.open` stopped counting as user-initiated the moment
              an `await` went in front of it, so a popup blocker can
              swallow that tab without telling anyone. A plain link is
              the way out, and costs nothing when the tab did open. */}
          {authUrl && (
            <p className="setup-step-copy">
              <a className="setup-link" href={authUrl} target="_blank" rel="noopener noreferrer">
                No tab opened? Open the Plex sign-in
              </a>
            </p>
          )}
        </>
      )}

      {phase === 'linking' && <p className="setup-step-copy">Finishing setup…</p>}

      {error && <p className="setup-error">{error}</p>}
    </>
  )
}
