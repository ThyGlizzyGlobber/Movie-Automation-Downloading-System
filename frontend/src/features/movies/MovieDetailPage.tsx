import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getMovie, rejectCurrentMovieCopy } from '../../api/movies'
import { createRequest, rejectRequest } from '../../api/requests'
import MediaRow from '../../components/MediaRow'
import RedownloadModal from '../../components/RedownloadModal'
import RequestModal from '../../components/RequestModal'
import ErrorState from '../../components/ErrorState'
import Icon from '../../components/Icon'
import DetailShell, { DetailShellSkeleton, type DetailPill, type DetailRow, type DetailTile } from '../detail/DetailShell'
import { acrossReleases, DetailCast, DetailCastSkeleton, DetailTrailer, DetailTrailerSkeleton, FactTiles, filedOnLabel, genreLinks, peopleLinks, qualityFromName, serviceTile, sourceFromName, usePlexHref, useTitleRequests } from '../detail/DetailBits'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { certificationOf, languageNameOf, yearOf } from '../../lib/detailHelpers'
import { moviePill } from '../../lib/homeHero'
import { formatBytes } from '../../lib/format'
import { NON_TERMINAL, statusMeta } from '../../lib/status'
import { errorText, useToast } from '../../lib/toast'
import type { RedownloadMode } from '../../types/requests'
import '../detail/DetailPage.css'

function runtimeLabel(runtime: number | null): string {
  if (!runtime) return ''
  const h = Math.floor(runtime / 60)
  const m = runtime % 60
  return h ? `${h}h ${m}m` : `${m}m`
}

// A movie page's usual shape: Status and Requested by, seven facts,
// score / year / rating / runtime pills then genres, and the one Add to
// Plex button a title not yet on Plex has.
function MovieDetailSkeleton() {
  return (
    <>
      <DetailShellSkeleton
        shape={{ tiles: [{}, { fit: true }], facts: 7, pills: ['7.4 · 1,234', '2024', 'PG-13', '2h 10m'], buttons: ['Add to Plex'] }}
        aside={<DetailCastSkeleton />}
      >
        <DetailTrailerSkeleton />
      </DetailShellSkeleton>
      <div className="detail-rows">
        <MediaRow loading title="More" qualifier="like this" items={[]} mediaType="movie" />
      </div>
    </>
  )
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
  const plexHref = usePlexHref('movie', tmdbId, title, year, !!movie?.on_plex)

  if (movieQuery.isLoading) return <MovieDetailSkeleton />
  if (movieQuery.isError || !movie) {
    return <ErrorState message={movieQuery.error instanceof Error ? movieQuery.error.message : undefined} retryHref="#/movies" />
  }

  async function submit(redownloadMode?: RedownloadMode) {
    setBusy('request')
    try {
      await createRequest({
        tmdb_id: tmdbId,
        query: title || null,
        redownload_mode: redownloadMode ?? null,
      })
      queryClient.invalidateQueries({ queryKey: ['requests'] })
      toast({ tone: 'info', title: `Adding ${title} to Plex`, body: 'Looking for the best copy now' })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't request that", body: errorText(err) })
    } finally {
      setBusy(null)
    }
  }

  const active = requests.find((r) => NON_TERMINAL.has(r.status)) ?? null
  const certification = certificationOf(movie)
  const genres = (movie.genres ?? []).slice(0, 3)

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
  if (genres.length) pills.push({ text: genreLinks(genres, 'movie'), mute: true })

  const sideTiles: DetailTile[] = []
  const service = serviceTile(movie, false)
  if (service) sideTiles.push(service)
  sideTiles.push(
    movie.on_plex
      ? { label: 'Status', value: (<><Icon name="check-circle" />Available on Plex</>), tone: 'mint' }
      : { label: 'Status', value: movie.is_coming_soon ? 'Coming soon' : active ? 'On the way' : 'Not on Plex yet', tone: active ? 'ice' : 'dim' },
  )
  if (requests[0]?.requested_by_username) sideTiles.push({ label: 'Requested by', value: requests[0].requested_by_username, fit: true })

  // From the library ledger, not a completed request's recorded winner.
  // A request row is deleted by "Clear My Requests" and by retention, so
  // these tiles used to disappear while the file was still on disk; the
  // ledger outlives history. Its size is also the real on-disk one
  // rather than the size the torrent advertised, which differ whenever a
  // release bundled a sample or subtitles.
  const library = movie.library
  const fileTiles: DetailTile[] | null =
    library && library.files > 0
      ? [
          { label: 'Quality', value: acrossReleases(library.releases, qualityFromName), tone: 'mint' },
          { label: 'Size', value: library.total_bytes ? formatBytes(library.total_bytes) : '—' },
          { label: 'Source', value: acrossReleases(library.releases, sourceFromName) },
          { label: 'Added', value: filedOnLabel(library.added_at) },
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
  if (movie.genres?.length) details.push({ label: 'Genres', value: genreLinks(movie.genres, 'movie', ', ') })
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
          Play on Plex
        </a>
      ) : (
        <span className="btn pri dis">
          <Icon name="play" />
          Play on Plex
        </span>
      )}
      <button className="btn sec" disabled={!!busy} onClick={() => setModalOpen(true)}>
        <Icon name="refresh" />
        Re-download
      </button>
    </>
  ) : (
    <>
      {/* Once requested, the button itself shows where the request is:
          searching, downloading with its percentage, and so on. */}
      {active ? (
        <span className={`btn pri dis detail-live ${statusMeta(active.status).cls}`}>
          <Icon name={statusMeta(active.status).icon} />
          {statusMeta(active.status).label}
          {active.status === 'downloading' && active.download_progress != null ? ` ${Math.round(active.download_progress * 100)}%` : ''}
        </span>
      ) : (
        <button className="btn pri" disabled={!!busy} onClick={() => setRequestOpen(true)}>
          <Icon name="plus" />
          {busy ? 'Adding…' : 'Add to Plex'}
        </button>
      )}
    </>
  )

  return (
    <>
      <DetailShell
        backdropPath={movie.backdrop_path}
        posterPath={movie.poster_path}
        logoPath={movie.logo_path}
        title={title}
        onPlex={!!movie.on_plex}
        pill={moviePill(movie)}
        pills={pills}
        overview={movie.overview}
        actions={actions}
        tiles={sideTiles}
        details={details}
        aside={<DetailCast cast={movie.credits?.cast} limit={8} />}
      >
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
        onSubmit={() => {
          setRequestOpen(false)
          submit()
        }}
      />
      <RedownloadModal
        open={modalOpen}
        targetLabel={title}
        trackedAvailable={movie.on_plex_tracked || !!movie.plex_file_available}
        canReject
        onClose={() => setModalOpen(false)}
        onChoose={async (mode) => {
          setModalOpen(false)
          if (mode === 'reject') {
            // Bin the copy (blacklisting its release), then ask afresh. A copy
            // still downloading is rejected by its request; a filed one through
            // the library ledger (or, for a file Obsidian never added, the file
            // Plex points at).
            const downloading = requests.find((r) => r.status === 'downloading')
            try {
              if (downloading) await rejectRequest(downloading.id)
              else await rejectCurrentMovieCopy(tmdbId)
            } catch (err) {
              toast({ tone: 'error', title: "Couldn't bin that copy", body: errorText(err) })
              return
            }
            queryClient.invalidateQueries({ queryKey: ['requests'] })
            submit()
            return
          }
          submit(mode)
        }}
      />
    </>
  )
}
