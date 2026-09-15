import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listShows, pauseShow, resumeShow, deleteShow } from '../../api/tv'
import ProgressBar from '../../components/ProgressBar'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { posterUrl } from '../../lib/tmdbImage'
import { relativeTime } from '../../lib/format'
import { errorText, useToast } from '../../lib/toast'
import { latestRequestLabel } from '../../lib/requestGrouping'
import { statusMeta } from '../../lib/status'
import type { ShowOut } from '../../types/shows'
import type { RequestOut } from '../../types/requests'
import '../requests/RequestsPage.css'
import '../../components/BrowsePage.css'
import './WatchingPage.css'

function SubscriptionStatusPill({ status }: { status: 'watching' | 'paused' }) {
  const cls = status === 'paused' ? 'status-queued' : 'status-complete'
  const label = status === 'paused' ? 'Paused' : 'Following'
  return (
    <span className={`status-pill ${cls}`}>
      <span className={`status-dot ${cls}`} />
      {label}
    </span>
  )
}

// The second pill on a Watching row: what the show's most recent
// episode/pack request actually did, or a plain "not checked yet" when
// there's no history (just subscribed, catch-up still queued).
function LatestRequestPill({ req }: { req: RequestOut | null }) {
  if (!req) {
    return (
      <span className="status-pill status-queued">
        <span className="status-dot status-queued" />
        Not checked yet
      </span>
    )
  }
  const meta = statusMeta(req.status)
  return (
    <span className={`status-pill ${meta.cls}`}>
      <span className={`status-dot ${meta.cls}`} />
      {latestRequestLabel(req)} — {meta.label}
    </span>
  )
}

function WatchingRow({ show, onChanged }: { show: ShowOut; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const { toast } = useToast()
  const req = show.latest_request

  async function handleToggle() {
    setBusy(true)
    try {
      if (show.status === 'paused') await resumeShow(show.id)
      else await pauseShow(show.id)
      onChanged()
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't update this show", body: errorText(err) })
    } finally {
      setBusy(false)
    }
  }

  async function handleUnsubscribe() {
    if (!confirm('Stop following this show? Its downloads stay in Requests.')) return
    setBusy(true)
    try {
      await deleteShow(show.id)
      onChanged()
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't stop following", body: errorText(err) })
      setBusy(false)
    }
  }

  return (
    <div className="req-row">
      <img className="req-poster" src={posterUrl(show.poster_path)} alt="" />
      <div className="req-main">
        <a className="req-title" href={`#/tv/${show.tmdb_id}`}>
          {show.title}
        </a>
        <div className="req-sub">Last checked {relativeTime(show.last_checked_at)}</div>
        <div className="watch-pills">
          <SubscriptionStatusPill status={show.status} />
          <LatestRequestPill req={req} />
        </div>
        {req?.status === 'downloading' && req.download_progress != null && <ProgressBar progress={req.download_progress} />}
      </div>
      <div className="req-actions">
        <button className="toggle-show-btn" disabled={busy} onClick={handleToggle}>
          {show.status === 'paused' ? 'Resume' : 'Pause'}
        </button>
        <button className="watch-link" disabled={busy} onClick={handleUnsubscribe}>
          Stop following
        </button>
      </div>
    </div>
  )
}

export default function WatchingPage() {
  usePageTitle('Following')
  useSetHasHero(false)
  const queryClient = useQueryClient()
  const showsQuery = useQuery({ queryKey: ['shows'], queryFn: () => listShows() })

  function onChanged() {
    queryClient.invalidateQueries({ queryKey: ['shows'] })
  }

  if (showsQuery.isLoading) return <LoadingState />
  if (showsQuery.isError) {
    return <ErrorState message={showsQuery.error instanceof Error ? showsQuery.error.message : undefined} retryHref="#/tv" />
  }

  const shows = showsQuery.data ?? []
  if (!shows.length) {
    return <EmptyState message="No shows yet. Open a show and tap Add show." />
  }

  return (
    <div className="watching">
      <div className="browse-head">
        <div>
          <h1 className="browse-title">Following</h1>
          <p className="browse-sub">
            {shows.length} show{shows.length === 1 ? '' : 's'} · new episodes are picked up on their own
          </p>
        </div>
      </div>
      <div id="watchingList">
        {shows.map((s) => (
          <WatchingRow key={s.id} show={s} onChanged={onChanged} />
        ))}
      </div>
    </div>
  )
}
