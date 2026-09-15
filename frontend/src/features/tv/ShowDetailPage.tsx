import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getTvShow, listShows, createShow, pauseShow, resumeShow, deleteShow, bulkDownload } from '../../api/tv'
import RequestModal from '../../components/RequestModal'
import EpisodeList from './EpisodeList'
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
import { errorText, useToast } from '../../lib/toast'
import { posterUrl, backdropUrl } from '../../lib/tmdbImage'
import AmbientGlow from '../../components/AmbientGlow'
import { genreLine, languageNameOf, tvCertificationOf, yearOf } from '../../lib/detailHelpers'
import type { ShowOut } from '../../types/shows'
import type { RedownloadMode, RequestOut } from '../../types/requests'
import '../detail/DetailPage.css'
import Icon from '../../components/Icon'

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

  const [activeSeason, setActiveSeason] = useState<number | null>(null)
  const [requestModal, setRequestModal] = useState<PendingBulk | null>(null)
  const [bulkPhase, setBulkPhase] = useState<Phase>('idle')
  const [bulkError, setBulkError] = useState<string | null>(null)
  const [bulkResult, setBulkResult] = useState<RequestOut | null>(null)
  const [bulkBusyKey, setBulkBusyKey] = useState<string | null>(null)
  const [pendingBulk, setPendingBulk] = useState<PendingBulk | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const { toast } = useToast()

  if (showQuery.isLoading) return <LoadingState />
  if (showQuery.isError || !show) {
    return <ErrorState message={showQuery.error instanceof Error ? showQuery.error.message : undefined} />
  }

  async function handleSubscribeClick() {
    setSubscribeBusy(true)
    try {
      const sub = await createShow(tmdbId)
      setSubscription(sub)
      toast({ tone: 'ok', title: `Following ${show!.name || show!.original_name || 'this show'}`, body: 'New episodes are picked up on their own.' })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't add this show", body: errorText(err) })
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
    if (!confirm('Stop following this show? Its downloads stay in Requests.')) return
    await deleteShow(subscription.id)
    setSubscription(null)
  }

  async function runBulkDownload(
    scope: 'season' | 'series',
    seasonNumber: number | null,
    key: string,
    redownloadMode?: RedownloadMode,
    minResolution?: string | null,
    profileId?: string | null,
  ) {
    setBulkBusyKey(key)
    setBulkPhase('busy')
    try {
      const req = await bulkDownload(tmdbId, {
        scope,
        season_number: seasonNumber,
        min_resolution: minResolution ?? null,
        profile_id: profileId ?? null,
        redownload_mode: redownloadMode ?? null,
      })
      setBulkPhase('done')
      setBulkResult(req)
      toast({
        tone: 'info',
        title: `Requested ${show!.name || show!.original_name || 'this show'}`,
        body: `${scope === 'series' ? 'Whole series' : `Season ${seasonNumber}`} · looking for a copy now`,
      })
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
    // Not in Plex yet: the request sheet picks the quality profile.
    setBulkBusyKey(key)
    setRequestModal({ scope, seasonNumber, label })
  }

  const backdrop = backdropUrl(show.backdrop_path)
  const cast = show.credits?.cast
  const seasons = (show.seasons || []).filter((s) => s.season_number > 0)
  // Most recent real season first; specials only when that's all there is.
  const currentSeason = activeSeason ?? (seasons.length ? seasons[seasons.length - 1].season_number : null)
  const currentSeasonLabel = seasons.find((x) => x.season_number === currentSeason)?.name || `Season ${currentSeason}`
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
        <AmbientGlow posterPath={show.poster_path} />
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
                <Icon name={subscription ? 'close' : 'plus'} />
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
            <h2 className="section-heading">Episodes</h2>
            {seasons.length > 0 && (
              <div className="seg season-picker" role="tablist" aria-label="Season">
                {seasons.map((s) => (
                  <button
                    key={s.id}
                    role="tab"
                    aria-selected={s.season_number === currentSeason}
                    className={s.season_number === currentSeason ? 'active' : ''}
                    onClick={() => setActiveSeason(s.season_number)}
                  >
                    {s.name || `Season ${s.season_number}`}
                  </button>
                ))}
              </div>
            )}
            {currentSeason != null && <EpisodeList tmdbId={tmdbId} season={currentSeason} />}
            <div className="bulk-actions">
              {currentSeason != null && (
                <button
                  className="bulk-btn"
                  disabled={bulkPhase === 'busy' && bulkBusyKey === `season-${currentSeason}`}
                  onClick={() => handleBulkClick('season', currentSeason, currentSeasonLabel, `season-${currentSeason}`)}
                >
                  {bulkPhase === 'busy' && bulkBusyKey === `season-${currentSeason}` ? 'Adding…' : `Download ${currentSeasonLabel}`}
                </button>
              )}
              <button
                className="bulk-btn primary"
                disabled={bulkPhase === 'busy' && bulkBusyKey === 'series'}
                onClick={() => handleBulkClick('series', null, 'Complete Series', 'series')}
              >
                {bulkPhase === 'busy' && bulkBusyKey === 'series' ? 'Adding…' : 'Download Complete Series'}
              </button>
            </div>
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

      <RequestModal
        open={requestModal != null}
        title={requestModal?.scope === 'series' ? title : `${title} · ${requestModal?.label ?? ''}`}
        subtitle={requestModal?.scope === 'series' ? 'Complete series' : 'One season'}
        posterPath={show.poster_path}
        submitLabel="Download"
        onClose={() => {
          setRequestModal(null)
          setBulkBusyKey(null)
        }}
        onSubmit={(profile) => {
          const pending = requestModal
          setRequestModal(null)
          if (!pending) return
          const key = pending.scope === 'series' ? 'series' : `season-${pending.seasonNumber}`
          runBulkDownload(pending.scope, pending.seasonNumber, key, undefined, profile.min_resolution, profile.id)
        }}
      />
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
