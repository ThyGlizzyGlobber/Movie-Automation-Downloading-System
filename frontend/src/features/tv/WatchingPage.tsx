import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listShows, deleteShow } from '../../api/tv'
import ProgressBar from '../../components/ProgressBar'
import Img from '../../components/Img'
import { Skel, SkelText, SkelWords } from '../../components/Skeleton'
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

  async function handleUnsubscribe() {
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
      <Img className="req-poster" src={posterUrl(show.poster_path)} alt="" />
      <div className="req-main">
        <a className="req-title" href={`#/tv/${show.tmdb_id}`}>
          {show.title}
        </a>
        <div className="req-sub">Last checked {relativeTime(show.last_checked_at)}</div>
        <div className="watch-pills">
          <LatestRequestPill req={req} />
        </div>
        {req?.status === 'downloading' && req.download_progress != null && <ProgressBar progress={req.download_progress} />}
      </div>
      <div className="req-actions">
        <button className="watch-link" disabled={busy} onClick={handleUnsubscribe}>
          Unfollow
        </button>
      </div>
    </div>
  )
}

const SKELETON_ROWS = 5

function WatchingRowSkeleton() {
  return (
    <div className="req-row" aria-hidden="true">
      <Skel className="req-poster" />
      <div className="req-main">
        <span className="req-title">
          <SkelText width="40%" />
        </span>
        <div className="req-sub">
          <SkelText width="9em" />
        </div>
        <div className="watch-pills">
          <Skel className="status-pill">
            <span className="status-dot" />
            S01E01 — On Plex
          </Skel>
        </div>
      </div>
      <div className="req-actions">
        <Skel className="watch-link">Unfollow</Skel>
      </div>
    </div>
  )
}

export default function WatchingPage() {
  usePageTitle('Following')
  useSetHasHero(false)
  const queryClient = useQueryClient()
  const showsQuery = useQuery({ queryKey: ['shows'], queryFn: () => listShows('watching') })

  function onChanged() {
    queryClient.invalidateQueries({ queryKey: ['shows'] })
  }

  const loading = showsQuery.isLoading
  if (showsQuery.isError) {
    return <ErrorState message={showsQuery.error instanceof Error ? showsQuery.error.message : undefined} retryHref="#/tv" />
  }

  const shows = showsQuery.data ?? []
  if (!loading && !shows.length) {
    return <EmptyState message="No shows yet. Open a show and tap Add show." />
  }

  return (
    <div className="watching">
      <div className="browse-head">
        <div>
          <h1 className="browse-title">Following</h1>
          <p className="browse-sub">
            {loading ? <SkelWords text="4 shows · new episodes are picked up on their own" /> : `${shows.length} show${shows.length === 1 ? '' : 's'} · new episodes are picked up on their own`}
          </p>
        </div>
      </div>
      <div id="watchingList" aria-busy={loading || undefined}>
        {loading
          ? Array.from({ length: SKELETON_ROWS }, (_, i) => <WatchingRowSkeleton key={i} />)
          : shows.map((s) => <WatchingRow key={s.id} show={s} onChanged={onChanged} />)}
      </div>
    </div>
  )
}
