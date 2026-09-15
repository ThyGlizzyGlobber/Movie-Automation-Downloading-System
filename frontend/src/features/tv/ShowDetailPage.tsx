import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getTvShow, listShows, createShow, pauseShow, resumeShow, deleteShow, bulkDownload } from '../../api/tv'
import StatusPill from '../../components/StatusPill'
import ScoreRing from '../../components/ScoreRing'
import ClampedText from '../../components/ClampedText'
import CastRow from '../../components/CastRow'
import MediaRow from '../../components/MediaRow'
import RedownloadModal from '../../components/RedownloadModal'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { DetailOverviewPanel, CreatorsAndCastPanel, DetailsCardPanel, CastLine, DetailProviderIcon } from '../detail/DetailPanels'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { posterUrl, backdropUrl } from '../../lib/tmdbImage'
import { genreLine, languageNameOf, tvCertificationOf, yearOf } from '../../lib/detailHelpers'
import type { ShowOut } from '../../types/shows'
import type { RedownloadMode, RequestOut } from '../../types/requests'
import '../detail/DetailPage.css'

type Phase = 'idle' | 'busy' | 'done' | 'error'

interface PendingBulk {
  scope: 'season' | 'series'
  seasonNumber: number | null
  label: string
}

export default function ShowDetailPage() {
  const { id } = useParams()
  const tmdbId = Number(id)
  useSetHasHero(true)

  const showQuery = useQuery({ queryKey: ['tv', tmdbId], queryFn: () => getTvShow(tmdbId) })
  const showsQuery = useQuery({ queryKey: ['shows'], queryFn: () => listShows() })
  const show = showQuery.data
  usePageTitle(show ? show.name || show.original_name || null : null)

  const [subscription, setSubscription] = useState<ShowOut | null>(null)
  const [subscribeBusy, setSubscribeBusy] = useState(false)
  useEffect(() => {
    if (showsQuery.data) setSubscription(showsQuery.data.find((s) => s.tmdb_id === tmdbId) ?? null)
  }, [showsQuery.data, tmdbId])

  const [minResolution, setMinResolution] = useState('')
  const [bulkPhase, setBulkPhase] = useState<Phase>('idle')
  const [bulkError, setBulkError] = useState<string | null>(null)
  const [bulkResult, setBulkResult] = useState<RequestOut | null>(null)
  const [bulkBusyKey, setBulkBusyKey] = useState<string | null>(null)
  const [pendingBulk, setPendingBulk] = useState<PendingBulk | null>(null)
  const [modalOpen, setModalOpen] = useState(false)

  if (showQuery.isLoading) return <LoadingState />
  if (showQuery.isError || !show) {
    return <ErrorState message={showQuery.error instanceof Error ? showQuery.error.message : undefined} />
  }

  async function handleSubscribeClick() {
    setSubscribeBusy(true)
    try {
      const sub = await createShow(tmdbId)
      setSubscription(sub)
    } catch (err) {
      alert(`Couldn't add this show: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setSubscribeBusy(false)
    }
  }

  async function handlePause() {
    if (!subscription) return
    setSubscription(await pauseShow(subscription.id))
  }

  async function handleResume() {
    if (!subscription) return
    setSubscription(await resumeShow(subscription.id))
  }

  async function handleUnsubscribe() {
    if (!subscription) return
    if (!confirm("Unsubscribe from this show? Its download history stays in Requests.")) return
    await deleteShow(subscription.id)
    setSubscription(null)
  }

  async function runBulkDownload(scope: 'season' | 'series', seasonNumber: number | null, key: string, redownloadMode?: RedownloadMode) {
    setBulkBusyKey(key)
    setBulkPhase('busy')
    try {
      const req = await bulkDownload(tmdbId, {
        scope,
        season_number: seasonNumber,
        min_resolution: minResolution || null,
        redownload_mode: redownloadMode ?? null,
      })
      setBulkPhase('done')
      setBulkResult(req)
      // The backend may have just silently created a paused show record
      // to anchor this download (no prior subscription) — re-check so
      // the hero's own subscribe state stops showing stale info.
      if (!subscription) {
        const shows = await listShows()
        setSubscription(shows.find((s) => s.tmdb_id === tmdbId) ?? null)
      }
    } catch (err) {
      setBulkPhase('error')
      setBulkError(err instanceof Error ? err.message : 'Unknown error')
    }
  }

  function handleBulkClick(scope: 'season' | 'series', seasonNumber: number | null, label: string, key: string) {
    if (show!.on_plex) {
      setPendingBulk({ scope, seasonNumber, label })
      setBulkBusyKey(key)
      setModalOpen(true)
      return
    }
    runBulkDownload(scope, seasonNumber, key)
  }

  const backdrop = backdropUrl(show.backdrop_path)
  const cast = show.credits?.cast
  const seasons = (show.seasons || []).filter((s) => s.season_number > 0)
  const year = yearOf(show.first_air_date)
  const title = show.name || show.original_name || ''
  const certification = tvCertificationOf(show)

  const detailsRows: [string, string][] = []
  if (certification) detailsRows.push(['Certification', certification])
  const lang = languageNameOf(show.original_language)
  if (lang) detailsRows.push(['Original Language', lang])
  if (show.status) detailsRows.push(['Release Status', show.status])

  const isPaused = subscription?.status === 'paused'
  const stateIconDisabled = !subscription && show.is_coming_soon

  return (
    <>
      <div className="detail-hero">
        {backdrop && <img className="detail-hero-backdrop" src={backdrop} alt="" />}
        <div className="detail-hero-fade" />
        <div className="detail-hero-content">
          <div className="detail-action-col">
            <div className="detail-poster-wrap">
              <img className="detail-poster" src={posterUrl(show.poster_path)} alt="" />
              {show.on_plex && <div className="on-plex-badge">On Plex</div>}
            </div>
            <div className="detail-icon-row">
              <DetailProviderIcon item={show} isTv />
              <ScoreRing voteAverage={show.vote_average} />
              <button
                className="detail-state-icon"
                title={stateIconDisabled ? 'Not out yet' : subscription ? 'Unsubscribe' : 'Add Show'}
                aria-label={subscription ? 'Unsubscribe' : 'Add Show'}
                disabled={stateIconDisabled}
                onClick={subscription ? handleUnsubscribe : handleSubscribeClick}
              >
                <span className="material-symbols-rounded">{subscription ? 'close' : 'add'}</span>
              </button>
            </div>
            {!subscription && show.is_coming_soon ? (
              <button className="add-btn" disabled title="Not out digitally yet">
                Coming Soon
              </button>
            ) : !subscription ? (
              <button className="add-btn" disabled={subscribeBusy} onClick={handleSubscribeClick}>
                {subscribeBusy ? 'Adding…' : '+ Add Show'}
              </button>
            ) : isPaused ? (
              <button className="add-btn" onClick={handleResume}>
                Paused — Tap to Resume
              </button>
            ) : (
              <button className="add-btn" onClick={handlePause}>
                Watching &#10003;
              </button>
            )}
            {subscription && (
              <button className="unsubscribe-link" onClick={handleUnsubscribe}>
                Unsubscribe
              </button>
            )}
          </div>
          <div className="detail-info">
            <h1>
              {title}
              {year && <span className="hero-year"> ({year})</span>}
            </h1>
            <div className="detail-meta">
              {show.status && <span className="cert-badge">{show.status}</span>}
              <span>{genreLine(show.genres)}</span>
              {year && <span>{year}</span>}
            </div>
            <div className="detail-overview-row">
              <div className="detail-overview-col">
                <ClampedText text={show.overview ?? ''} textClassName="detail-overview" buttonClassName="detail-overview-more-btn" />
              </div>
              <CastLine cast={cast} />
            </div>
          </div>
        </div>
      </div>

      <MediaRow title="Related" items={show.recommendations?.results ?? []} mediaType="tv" />

      <div className="detail-panels-grid">
        <div className="detail-panels-col">
          <div className="detail-panel">
            <DetailOverviewPanel title={title} genres={show.genres} overview={show.overview} />
          </div>
          <div className="detail-panel">
            <CreatorsAndCastPanel crew={show.credits?.crew} cast={cast} studio={show.production_companies?.[0]?.name} />
          </div>
        </div>
        <div className="detail-panels-col">
          <div className="detail-panel">
            <h2 className="section-heading">Bulk download</h2>
            <select
              className="resolution-select"
              value={minResolution}
              onChange={(e) => setMinResolution(e.target.value)}
              aria-label="Minimum resolution"
            >
              <option value="">Default quality</option>
              <option value="2160p">2160p / 4K minimum</option>
              <option value="1080p">1080p minimum</option>
            </select>
            {seasons.length > 0 && (
              <div className="season-list">
                {seasons.map((s) => {
                  const key = `season-${s.season_number}`
                  return (
                    <div className="season-row" key={s.id}>
                      <span>{s.name || `Season ${s.season_number}`}</span>
                      <button
                        className="season-btn"
                        disabled={bulkPhase === 'busy' && bulkBusyKey === key}
                        onClick={() => handleBulkClick('season', s.season_number, s.name || `Season ${s.season_number}`, key)}
                      >
                        {bulkPhase === 'busy' && bulkBusyKey === key ? 'Adding…' : 'Download'}
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
            <button
              className="bulk-btn primary"
              disabled={bulkPhase === 'busy' && bulkBusyKey === 'series'}
              onClick={() => handleBulkClick('series', null, 'Complete Series', 'series')}
            >
              {bulkPhase === 'busy' && bulkBusyKey === 'series' ? 'Adding…' : 'Download Complete Series'}
            </button>
            {(bulkPhase === 'done' || bulkPhase === 'error') && (
              <div className="add-confirm">
                {bulkPhase === 'error' ? (
                  <span>Couldn't add that: {bulkError}</span>
                ) : (
                  <>
                    <StatusPill status={bulkResult?.status ?? 'queued'} />
                    <span>
                      Added to <a href="#/requests">Requests</a>.
                    </span>
                  </>
                )}
              </div>
            )}
          </div>
          <div className="detail-panel">
            <DetailsCardPanel rows={detailsRows} />
          </div>
        </div>
      </div>

      <CastRow cast={cast} />

      <div className="detail-footer">
        <span className="brand-wordmark">
          <img className="brand-mark" src="/brand-icon.svg" alt="" />
          Meridian
        </span>
      </div>

      <RedownloadModal
        open={modalOpen}
        targetLabel={pendingBulk?.label ?? title}
        trackedAvailable={show.on_plex_tracked}
        onClose={() => setModalOpen(false)}
        onChoose={(mode) => {
          setModalOpen(false)
          if (pendingBulk) runBulkDownload(pendingBulk.scope, pendingBulk.seasonNumber, pendingBulk.scope === 'series' ? 'series' : `season-${pendingBulk.seasonNumber}`, mode)
        }}
      />
    </>
  )
}
