import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getMovie } from '../../api/movies'
import { createRequest, getRequest } from '../../api/requests'
import StatusPill from '../../components/StatusPill'
import ScoreRing from '../../components/ScoreRing'
import ClampedText from '../../components/ClampedText'
import CastRow from '../../components/CastRow'
import MediaRow from '../../components/MediaRow'
import RedownloadModal from '../../components/RedownloadModal'
import RequestModal from '../../components/RequestModal'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { DetailOverviewPanel, CreatorsAndCastPanel, DetailsCardPanel, CastLine, DetailProviderIcon } from '../detail/DetailPanels'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { posterUrl, backdropUrl } from '../../lib/tmdbImage'
import AmbientGlow from '../../components/AmbientGlow'
import { certificationOf, genreLine, languageNameOf, yearOf, statusFollowupText } from '../../lib/detailHelpers'
import { NON_TERMINAL } from '../../lib/status'
import { useToast } from '../../lib/toast'
import type { RedownloadMode, RequestOut } from '../../types/requests'
import '../detail/DetailPage.css'
import Icon from '../../components/Icon'

type AddPhase = 'idle' | 'adding' | 'added' | 'error'

export default function MovieDetailPage() {
  const { id } = useParams()
  const tmdbId = Number(id)
  useSetHasHero(true)

  const movieQuery = useQuery({ queryKey: ['movie', tmdbId], queryFn: () => getMovie(tmdbId) })
  const movie = movieQuery.data
  usePageTitle(movie ? movie.title || movie.original_title || null : null)

  const [addPhase, setAddPhase] = useState<AddPhase>('idle')
  const [addError, setAddError] = useState<string | null>(null)
  const [addRequestId, setAddRequestId] = useState<number | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [requestOpen, setRequestOpen] = useState(false)

  const [qualityPhase, setQualityPhase] = useState<AddPhase>('idle')
  const [qualityError, setQualityError] = useState<string | null>(null)
  const [qualityRequestId, setQualityRequestId] = useState<number | null>(null)
  const [qualityButton, setQualityButton] = useState<'2160p' | '1080p' | null>(null)

  const addRequestQuery = useQuery({
    queryKey: ['request', addRequestId],
    queryFn: () => getRequest(addRequestId as number),
    enabled: addRequestId != null,
    refetchInterval: (q) => (q.state.data && NON_TERMINAL.has(q.state.data.status) ? 4000 : false),
  })
  const qualityRequestQuery = useQuery({
    queryKey: ['request', qualityRequestId],
    queryFn: () => getRequest(qualityRequestId as number),
    enabled: qualityRequestId != null,
    refetchInterval: (q) => (q.state.data && NON_TERMINAL.has(q.state.data.status) ? 4000 : false),
  })

  const { toast } = useToast()

  if (movieQuery.isLoading) return <LoadingState />
  if (movieQuery.isError || !movie) {
    return <ErrorState message={movieQuery.error instanceof Error ? movieQuery.error.message : undefined} />
  }

  async function submitAdd(minResolution?: string, redownloadMode?: RedownloadMode, profileId?: string) {
    setAddPhase('adding')
    try {
      const req: RequestOut = await createRequest({
        tmdb_id: tmdbId,
        query: movie!.title || movie!.original_title || null,
        min_resolution: minResolution ?? null,
        redownload_mode: redownloadMode ?? null,
        profile_id: profileId ?? null,
      })
      setAddPhase('added')
      setAddRequestId(req.id)
      toast({ tone: 'info', title: `Requested ${movie!.title || movie!.original_title || 'this movie'}`, body: 'Looking for a copy now' })
    } catch (err) {
      setAddPhase('error')
      setAddError(err instanceof Error ? err.message : 'Unknown error')
    }
  }

  async function submitQuality(minResolution: '2160p' | '1080p') {
    setQualityButton(minResolution)
    setQualityPhase('adding')
    try {
      const req: RequestOut = await createRequest({
        tmdb_id: tmdbId,
        query: movie!.title || movie!.original_title || null,
        min_resolution: minResolution,
      })
      setQualityPhase('added')
      setQualityRequestId(req.id)
      toast({ tone: 'info', title: `Requested ${movie!.title || movie!.original_title || 'this movie'}`, body: `${minResolution === '2160p' ? '4K' : '1080p'} · looking for a copy now` })
    } catch (err) {
      setQualityPhase('error')
      setQualityError(err instanceof Error ? err.message : 'Unknown error')
    }
  }

  // Already in Plex: the redownload question comes first (existing
  // modal). Otherwise the request sheet picks a quality profile.
  function handleAddClick() {
    if (movie!.on_plex) {
      setModalOpen(true)
      return
    }
    setRequestOpen(true)
  }

  const runtime = movie.runtime ? `${Math.floor(movie.runtime / 60)}h ${movie.runtime % 60}m` : ''
  const backdrop = backdropUrl(movie.backdrop_path)
  const certification = certificationOf(movie)
  const cast = movie.credits?.cast
  const year = yearOf(movie.release_date)
  const title = movie.title || movie.original_title || ''
  const addRequest = addRequestQuery.data
  const addStatus = addRequest?.status
  const qualityRequest = qualityRequestQuery.data
  const qualityStatus = qualityRequest?.status

  const detailsRows: [string, string][] = []
  if (certification) detailsRows.push(['Certification', certification])
  const lang = languageNameOf(movie.original_language)
  if (lang) detailsRows.push(['Original Language', lang])
  detailsRows.push(['Release Status', movie.is_coming_soon ? 'Coming Soon' : 'Released'])

  return (
    <>
      <div className="detail-hero">
        <AmbientGlow posterPath={movie.poster_path} />
        {backdrop && <img className="detail-hero-backdrop" src={backdrop} alt="" />}
        <div className="detail-hero-fade" />
        <div className="detail-hero-content">
          <div className="detail-action-col">
            <div className="detail-poster-wrap">
              <img className="detail-poster" src={posterUrl(movie.poster_path)} alt="" />
              {movie.on_plex && <div className="on-plex-badge">On Plex</div>}
            </div>
            <div className="detail-icon-row">
              <DetailProviderIcon item={movie} isTv={false} />
              <ScoreRing voteAverage={movie.vote_average} />
              <button
                className="detail-state-icon"
                title={movie.is_coming_soon ? 'Not out digitally yet' : movie.on_plex ? 'Added to Plex' : 'Add to Plex'}
                aria-label="Add to Plex"
                disabled={movie.is_coming_soon || addPhase === 'added'}
                onClick={handleAddClick}
              >
                <Icon name={addPhase === 'added' || movie.on_plex ? 'check' : 'plus'} />
              </button>
            </div>
            {movie.is_coming_soon ? (
              <button className="add-btn" disabled title="Not out digitally yet">
                Coming Soon
              </button>
            ) : (
              <button className="add-btn" disabled={addPhase === 'adding' || addPhase === 'added'} onClick={handleAddClick}>
                {addPhase === 'adding' ? 'Adding…' : addPhase === 'added' ? 'Added' : movie.on_plex ? 'Added to Plex' : '+ Add to Plex'}
              </button>
            )}
          </div>
          <div className="detail-info">
            <h1>
              {title}
              {year && <span className="hero-year"> ({year})</span>}
            </h1>
            <div className="detail-meta">
              {certification && <span className="cert-badge">{certification}</span>}
              <span>{genreLine(movie.genres)}</span>
              {runtime && <span>{runtime}</span>}
              {year && <span>{year}</span>}
            </div>
            <div className="detail-overview-row">
              <div className="detail-overview-col">
                <ClampedText text={movie.overview ?? ''} textClassName="detail-overview" buttonClassName="detail-overview-more-btn" />
              </div>
              <CastLine cast={cast} />
            </div>
          </div>
        </div>
      </div>

      {(addPhase === 'added' || addPhase === 'error') && (
        <div className="add-confirm">
          {addPhase === 'error' ? (
            <span>Couldn't add that: {addError}</span>
          ) : (
            <>
              <StatusPill status={addStatus ?? 'queued'} />
              <span>{statusFollowupText(addStatus ?? 'queued')}</span>
            </>
          )}
        </div>
      )}

      <MediaRow title="Related" items={movie.recommendations?.results ?? []} mediaType="movie" />

      <div className="detail-panels-grid">
        <div className="detail-panels-col">
          <div className="detail-panel">
            <DetailOverviewPanel title={title} genres={movie.genres} overview={movie.overview} />
          </div>
          <div className="detail-panel">
            <CreatorsAndCastPanel crew={movie.credits?.crew} cast={cast} studio={movie.production_companies?.[0]?.name} />
          </div>
        </div>
        <div className="detail-panels-col">
          <div className="detail-panel">
            <h2 className="section-heading">Download</h2>
            <button className="bulk-btn" disabled={movie.is_coming_soon || qualityPhase === 'adding'} onClick={() => submitQuality('2160p')}>
              {qualityPhase === 'adding' && qualityButton === '2160p' ? 'Adding…' : 'Download 4K'}
            </button>
            <button className="bulk-btn" disabled={movie.is_coming_soon || qualityPhase === 'adding'} onClick={() => submitQuality('1080p')}>
              {qualityPhase === 'adding' && qualityButton === '1080p' ? 'Adding…' : 'Download 1080p'}
            </button>
            {(qualityPhase === 'added' || qualityPhase === 'error') && (
              <div className="add-confirm">
                {qualityPhase === 'error' ? (
                  <span>Couldn't add that: {qualityError}</span>
                ) : (
                  <>
                    <StatusPill status={qualityStatus ?? 'queued'} />
                    <span>{statusFollowupText(qualityStatus ?? 'queued')}</span>
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
        open={requestOpen}
        title={title}
        subtitle={[year, 'Movie', 'Not in Plex'].filter(Boolean).join(' · ')}
        posterPath={movie.poster_path}
        onClose={() => setRequestOpen(false)}
        onSubmit={(profile) => {
          setRequestOpen(false)
          submitAdd(profile.min_resolution ?? undefined, undefined, profile.id)
        }}
      />
      <RedownloadModal
        open={modalOpen}
        targetLabel={title}
        trackedAvailable={movie.on_plex_tracked}
        onClose={() => setModalOpen(false)}
        onChoose={(mode) => {
          setModalOpen(false)
          submitAdd(undefined, mode)
        }}
      />
    </>
  )
}
