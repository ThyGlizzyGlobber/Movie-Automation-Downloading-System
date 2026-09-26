import { Link } from 'react-router-dom'
import { useCallback, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listShows, deleteShow } from '../../api/tv'
import Icon from '../../components/Icon'
import Img from '../../components/Img'
import PosterCard from '../../components/PosterCard'
import DownloadBar from '../../components/DownloadBar'
import RequestStatusChip from '../../components/RequestStatusChip'
import StatusPill from '../../components/StatusPill'
import { PosterCardSkeleton, SkelWords } from '../../components/Skeleton'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle } from '../../lib/chrome'
import { posterUrl } from '../../lib/tmdbImage'
import { relativeTime } from '../../lib/format'
import { errorText, useToast } from '../../lib/toast'
import { latestRequestLabel } from '../../lib/requestGrouping'
import { FAILED_STATES, statusDetail } from '../../lib/status'
import type { ShowOut } from '../../types/shows'
import '../requests/RequestsPage.css'
import '../../components/RequestModal.css'
import './WatchingPage.css'

// The caption's second line: which episode/pack the latest request is
// for, then what the chip can't say — the reason when it stopped short,
// otherwise when the show was last checked. "Not checked yet" when
// there's no history (just followed, catch-up still queued).
function latestLine(show: ShowOut): string {
  const req = show.latest_request
  if (!req) return 'Not checked yet'
  const stopped = FAILED_STATES.has(req.status) || req.status === 'downloaded, not filed'
  return `${latestRequestLabel(req)} | ${stopped ? statusDetail(req.status, req.download_progress, req.error_message) : `checked ${relativeTime(show.last_checked_at)}`}`
}

// A followed show as the Requests grid draws a title: the poster (to the
// show's page) with the latest request's state in its corner, or its
// download bar straight under it while an episode is coming down. The ⋯
// opens the sheet with the rest and Unfollow.
function FollowCard({ show, onOpen }: { show: ShowOut; onOpen: () => void }) {
  const req = show.latest_request
  const downloading = req?.status === 'downloading'
  return (
    <div className={`rq-card${downloading ? ' rq-live' : ''}`}>
      <PosterCard
        item={{ id: show.tmdb_id, title: show.title, poster_path: show.poster_path }}
        mediaType="tv"
        caption={false}
        chip={req ? <RequestStatusChip status={req.status} completeLabel="Up to date" /> : undefined}
      />
      {downloading && <DownloadBar progress={req.download_progress} className="rq-card-bar" />}
      <div className="rq-cap">
        <div className="rq-cap-text">
          <Link className="rq-cap-title" to={`/tv/${show.tmdb_id}`}>
            {show.title}
          </Link>
          {!downloading && <small title={req ? statusDetail(req.status, req.download_progress, req.error_message) : undefined}>{latestLine(show)}</small>}
        </div>
        <button className="rq-more" aria-label={`Details for ${show.title}`} onClick={onOpen}>
          <Icon name="more" />
        </button>
      </div>
    </div>
  )
}

// The sheet behind a card's ⋯, in the Requests sheet's form: since
// when, when it was last checked, what the latest episode did and why,
// and the two things to do — open the show, or stop following it.
function FollowSheet({ show, onClose, onChanged }: { show: ShowOut; onClose: () => void; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const { toast } = useToast()
  const req = show.latest_request
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  async function handleUnfollow() {
    setBusy(true)
    try {
      await deleteShow(show.id)
      onClose()
      onChanged()
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't stop following", body: errorText(err) })
      setBusy(false)
    }
  }

  const sub = [
    'Series',
    show.tmdb_status && show.tmdb_status !== 'Returning Series' ? show.tmdb_status : null,
    `Following since ${new Date(show.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`,
  ]
    .filter(Boolean)
    .join(' | ')
  const downloading = req?.status === 'downloading'

  return (
    <div
      className="request-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="request-modal rq-sheet" role="dialog" aria-modal="true" aria-labelledby="follow-sheet-title">
        <button className="request-modal-close" aria-label="Close" onClick={onClose}>
          <Icon name="close" />
        </button>
        <div className="request-modal-head">
          <Img className="request-modal-poster" src={posterUrl(show.poster_path)} alt="" />
          <div>
            <h2 id="follow-sheet-title" className="request-modal-title">
              {show.title}
            </h2>
            <p className="request-modal-sub">{sub}</p>
          </div>
        </div>

        <div className="rq-sheet-status">
          {req ? (
            // A download is said by its bar, so it gets no pill.
            <div className={`rq-sheet-line${downloading ? ' live' : ''}`}>
              {!downloading && <StatusPill status={req.status} />}
              <span>
                {latestRequestLabel(req)}
                {' | '}
                {downloading ? 'Downloading now' : statusDetail(req.status, req.download_progress, req.error_message)}
              </span>
            </div>
          ) : (
            <div className="rq-sheet-line">
              <span>Nothing requested yet. New episodes are picked up as they air.</span>
            </div>
          )}
          {downloading && <DownloadBar progress={req.download_progress} className="rq-sheet-bar" />}
        </div>

        <div className="request-modal-foot">
          <span className="request-modal-note">Checked {relativeTime(show.last_checked_at)}</span>
          <button className="btn sm danger" disabled={busy} onClick={handleUnfollow}>
            <Icon name="close" />
            {busy ? 'Unfollowing…' : 'Unfollow'}
          </button>
          <Link className="btn sm pri" to={`/tv/${show.tmdb_id}`} onClick={onClose}>
            Open show
            <Icon name="next" />
          </Link>
        </div>
      </div>
    </div>
  )
}

const SKELETON_CARDS = 10

export default function WatchingPage() {
  usePageTitle('Following')
  const queryClient = useQueryClient()
  // Polled like Requests, so a card's download bar moves while it's open.
  const showsQuery = useQuery({ queryKey: ['shows'], queryFn: () => listShows('watching'), refetchInterval: 5000 })
  const [openId, setOpenId] = useState<number | null>(null)
  const closeSheet = useCallback(() => setOpenId(null), [])

  function onChanged() {
    queryClient.invalidateQueries({ queryKey: ['shows'] })
  }

  const loading = showsQuery.isLoading
  if (showsQuery.isError) {
    return <ErrorState message={showsQuery.error instanceof Error ? showsQuery.error.message : undefined} retryHref="#/tv" />
  }

  const shows = showsQuery.data ?? []
  const openShow = openId != null ? shows.find((s) => s.id === openId) : undefined
  const downloading = shows.filter((s) => s.latest_request?.status === 'downloading').length
  const lead = [downloading ? `${downloading} downloading` : null, 'new episodes arrive on their own'].filter(Boolean).join(' | ')

  return (
    <div className="watching">
      <div className="rq-head">
        <h1 className="rq-h1">
          Following
          {shows.length > 0 && <span className="rq-count"> | {shows.length}</span>}
        </h1>
        <p className="rq-lead">{loading ? <SkelWords text="1 downloading | new episodes arrive on their own" /> : lead}</p>
      </div>

      {loading ? (
        <div className="grid category-grid" aria-busy="true">
          {Array.from({ length: SKELETON_CARDS }, (_, i) => (
            <PosterCardSkeleton key={i} />
          ))}
        </div>
      ) : shows.length === 0 ? (
        <EmptyState
          icon="tv"
          title="Not following anything"
          message="Open a show and tap Follow. New episodes are downloaded as they air."
          action={
            <Link className="retry" to="/browse?type=tv">
              <Icon name="search" />
              Browse shows
            </Link>
          }
        />
      ) : (
        <div className="grid category-grid">
          {shows.map((s) => (
            <FollowCard key={s.id} show={s} onOpen={() => setOpenId(s.id)} />
          ))}
        </div>
      )}

      {openShow && <FollowSheet show={openShow} onClose={closeSheet} onChanged={onChanged} />}
    </div>
  )
}
