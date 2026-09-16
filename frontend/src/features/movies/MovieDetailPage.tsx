import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getMovie } from '../../api/movies'
import { createRequest } from '../../api/requests'
import StatusPill from '../../components/StatusPill'
import MediaRow from '../../components/MediaRow'
import RedownloadModal from '../../components/RedownloadModal'
import RequestModal from '../../components/RequestModal'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import Icon from '../../components/Icon'
import DetailShell, { type DetailPill, type DetailRow, type DetailTile } from '../detail/DetailShell'
import { DetailCast, DetailTrailer, FactTiles, peopleLinks, qualityFromName, serviceTile, sourceFromName, usePlexHref, useTitleRequests } from '../detail/DetailBits'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { certificationOf, languageNameOf, yearOf, statusFollowupText } from '../../lib/detailHelpers'
import { formatBytes } from '../../lib/format'
import { NON_TERMINAL } from '../../lib/status'
import { errorText, useToast } from '../../lib/toast'
import type { RedownloadMode } from '../../types/requests'
import '../detail/DetailPage.css'

function runtimeLabel(runtime: number | null): string {
  if (!runtime) return ''
  const h = Math.floor(runtime / 60)
  const m = runtime % 60
  return h ? `${h}h ${m}m` : `${m}m`
}

export default function MovieDetailPage() {
  const { id } = useParams()
  const tmdbId = Number(id)
  useSetHasHero(true)
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const movieQuery = useQuery({ queryKey: ['movie', tmdbId], queryFn: () => getMovie(tmdbId) })
  const movie = movieQuery.data
  usePageTitle(movie ? movie.title || movie.original_title || null : null)

  const [busy, setBusy] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [requestOpen, setRequestOpen] = useState(false)

  const requests = useTitleRequests(tmdbId, ['movie'])
  const title = movie?.title || movie?.original_title || ''
  const year = yearOf(movie?.release_date)
  const plexHref = usePlexHref('movie', title, year, !!movie?.on_plex)

  if (movieQuery.isLoading) return <LoadingState />
  if (movieQuery.isError || !movie) {
    return <ErrorState message={movieQuery.error instanceof Error ? movieQuery.error.message : undefined} retryHref="#/movies" />
  }

  async function submit(redownloadMode?: RedownloadMode, notify?: boolean) {
    setBusy('request')
    try {
      await createRequest({
        tmdb_id: tmdbId,
        query: title || null,
        redownload_mode: redownloadMode ?? null,
        notify: notify ?? null,
      })
      queryClient.invalidateQueries({ queryKey: ['requests'] })
      toast({ tone: 'info', title: `Requested ${title}`, body: 'Looking for the best copy now' })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't request that", body: errorText(err) })
    } finally {
      setBusy(null)
    }
  }

  const active = requests.find((r) => NON_TERMINAL.has(r.status)) ?? null
  const latestDone = requests.find((r) => r.status === 'complete') ?? null
  const winner = (latestDone?.result as { winner?: { fileName?: string; fileSize?: number } } | null)?.winner
  const certification = certificationOf(movie)
  const genres = (movie.genres ?? []).slice(0, 3).map((g) => g.name).join(' · ')

  const eyebrow = movie.is_coming_soon
    ? { text: 'Coming soon', tone: 'amber' as const }
    : active
      ? { text: `${active.status === 'downloading' ? 'Downloading' : active.status === 'searching' ? 'Searching' : 'Queued'}${active.download_progress != null ? ` · ${Math.round(active.download_progress * 100)}%` : ''}`, tone: 'ice' as const }
      : movie.on_plex
        ? { text: `In Plex${winner ? ` · ${qualityFromName(winner.fileName)}` : ''}`.replace(/ · $/, ''), tone: 'mint' as const }
        : { text: '', tone: 'dim' as const }

  const pills: DetailPill[] = []
  if (movie.vote_average) {
    pills.push({
      star: true,
      text: (
        <>
          {movie.vote_average.toFixed(1)}
          {movie.vote_count ? <small>· {movie.vote_count.toLocaleString()}</small> : null}
        </>
      ),
    })
  }
  if (year) pills.push({ text: year })
  if (certification) pills.push({ text: certification })
  if (movie.runtime) pills.push({ text: runtimeLabel(movie.runtime) })
  if (genres) pills.push({ text: genres, mute: true })

  const sideTiles: DetailTile[] = []
  const service = serviceTile(movie, false)
  if (service) sideTiles.push(service)
  sideTiles.push(
    movie.on_plex
      ? { label: 'Library', value: (<><Icon name="check-circle" />Movies</>), tone: 'mint' }
      : { label: 'Status', value: movie.is_coming_soon ? 'Coming soon' : active ? 'On the way' : 'Not on Plex yet', tone: active ? 'ice' : 'dim' },
  )
  if (requests[0]?.requested_by_username) sideTiles.push({ label: 'Requested by', value: requests[0].requested_by_username })

  const fileTiles: DetailTile[] | null = winner
    ? [
        { label: 'Quality', value: qualityFromName(winner.fileName) || '—', tone: 'mint' },
        { label: 'Size', value: winner.fileSize ? formatBytes(winner.fileSize) : '—' },
        { label: 'Source', value: sourceFromName(winner.fileName) || '—' },
        { label: 'Added', value: latestDone ? new Date(latestDone.updated_at).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '—' },
      ]
    : null

  const crew = movie.credits?.crew ?? []
  const directors = crew.filter((c) => c.job === 'Director')
  const writers = crew.filter((c) => c.job === 'Screenplay' || c.job === 'Writer').slice(0, 3)
  const details: DetailRow[] = []
  if (certification) details.push({ label: 'Rated', value: certification })
  if (movie.release_date) details.push({ label: 'Released', value: new Date(`${movie.release_date}T00:00:00`).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) })
  const lang = languageNameOf(movie.original_language)
  if (lang) details.push({ label: 'Language', value: lang })
  if (movie.genres?.length) details.push({ label: 'Genres', value: movie.genres.map((g) => g.name).join(', ') })
  if (directors.length) details.push({ label: 'Director', value: peopleLinks(directors) })
  if (writers.length) details.push({ label: 'Writers', value: peopleLinks(writers) })
  if (movie.production_companies?.[0]) details.push({ label: 'Studio', value: movie.production_companies[0].name })

  const actions = movie.is_coming_soon ? (
    <span className="btn pri dis">
      <Icon name="clock" />
      Coming soon
    </span>
  ) : movie.on_plex ? (
    <>
      {plexHref ? (
        <a className="btn pri" href={plexHref} target="_blank" rel="noreferrer">
          <Icon name="play" />
          Play
        </a>
      ) : (
        <span className="btn pri dis">
          <Icon name="play" />
          Play
        </span>
      )}
      <button className="btn sec" disabled={!!busy} onClick={() => setModalOpen(true)}>
        <Icon name="refresh" />
        Re-download
      </button>
    </>
  ) : (
    <>
      <button className="btn pri" disabled={!!busy || !!active} onClick={() => setRequestOpen(true)}>
        <Icon name="plus" />
        {active ? 'Requested' : busy ? 'Adding…' : 'Request'}
      </button>
    </>
  )

  return (
    <>
      <DetailShell
        backTo="/movies"
        backLabel="Movies"
        backdropPath={movie.backdrop_path}
        posterPath={movie.poster_path}
        logoPath={movie.logo_path}
        title={title}
        onPlex={!!movie.on_plex}
        eyebrow={eyebrow.text}
        eyebrowTone={eyebrow.tone}
        pills={pills}
        overview={movie.overview}
        actions={actions}
        tiles={sideTiles}
        details={details}
        requests={requests}
      >
        {active && (
          <div className="detail-status">
            <StatusPill status={active.status} />
            <span>{statusFollowupText(active.status, active.error_message)}</span>
          </div>
        )}
        <DetailCast cast={movie.credits?.cast} />
        <DetailTrailer type="movie" tmdbId={tmdbId} backdropPath={movie.backdrop_path} />
        {fileTiles && (
          <>
            <h4 className="detail-h4">File</h4>
            <FactTiles tiles={fileTiles} />
          </>
        )}
      </DetailShell>

      <div className="detail-rows">
        <MediaRow title="More" qualifier="like this" items={movie.recommendations?.results ?? []} mediaType="movie" />
      </div>

      <RequestModal
        open={requestOpen}
        title={title}
        subtitle={[year, 'Movie', 'Not on Plex'].filter(Boolean).join(' · ')}
        posterPath={movie.poster_path}
        onClose={() => setRequestOpen(false)}
        onSubmit={(notify) => {
          setRequestOpen(false)
          submit(undefined, notify)
        }}
      />
      <RedownloadModal
        open={modalOpen}
        targetLabel={title}
        trackedAvailable={movie.on_plex_tracked}
        onClose={() => setModalOpen(false)}
        onChoose={(mode) => {
          setModalOpen(false)
          submit(mode)
        }}
      />
    </>
  )
}
