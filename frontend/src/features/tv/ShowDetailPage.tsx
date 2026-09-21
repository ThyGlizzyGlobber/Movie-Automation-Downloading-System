import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getTvShow, listShows, createShow, deleteShow, bulkDownload } from '../../api/tv'
import RequestModal from '../../components/RequestModal'
import EpisodeList, { EpisodesSectionSkeleton } from './EpisodeList'
import MediaRow from '../../components/MediaRow'
import RedownloadModal from '../../components/RedownloadModal'
import ErrorState from '../../components/ErrorState'
import Icon from '../../components/Icon'
import DetailShell, { DetailShellSkeleton, type DetailPill, type DetailRow, type DetailTile } from '../detail/DetailShell'
import { acrossReleases, DetailCast, DetailCastSkeleton, DetailTrailer, DetailTrailerSkeleton, FactTiles, genreLinks, peopleLinks, qualityFromName, sourceFromName, usePlexHref } from '../detail/DetailBits'
import { formatBytes } from '../../lib/format'
import { usePageTitle } from '../../lib/chrome'
import { errorText, useToast } from '../../lib/toast'
import { languageNameOf, tvCertificationOf, yearOf } from '../../lib/detailHelpers'
import { showPill, startOfToday } from '../../lib/homeHero'
import { originalServiceMatch } from '../../lib/providers'
import { useMediaQuery } from '../../lib/hooks'
import { useCertificationRegion } from '../auth/useSession'
import type { ShowOut } from '../../types/shows'
import type { RedownloadMode } from '../../types/requests'
import '../detail/DetailPage.css'

interface PendingBulk {
  scope: 'season' | 'series'
  seasonNumber: number | null
  label: string
}

// A show page's usual shape: Following, Seasons and a full-width Next
// episode tile, eight facts, four pills then genres, two buttons, then
// the Episodes section.
function ShowDetailSkeleton() {
  return (
    <>
      <DetailShellSkeleton
        shape={{ tiles: [{}, {}, { wide: true }], facts: 8, pills: ['7.9 · 1,234', '2024', 'TV-MA', '2 seasons'], buttons: ['Follow', 'Add all to Plex'] }}
        aside={<DetailCastSkeleton />}
      >
        <DetailTrailerSkeleton />
      </DetailShellSkeleton>
      <EpisodesSectionSkeleton />
      <div className="detail-rows">
        <MediaRow loading title="More" qualifier="like this" items={[]} mediaType="movie" />
      </div>
    </>
  )
}

export default function ShowDetailPage() {
  const { id } = useParams()
  const tmdbId = Number(id)
  const queryClient = useQueryClient()
  const { toast } = useToast()
  // Above every early return below — see MovieDetailPage's own note.
  const bannerShown = useMediaQuery('(min-width: 640px)')
  const region = useCertificationRegion()

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
  const [bulkBusyKey, setBulkBusyKey] = useState<string | null>(null)
  const [pendingBulk, setPendingBulk] = useState<PendingBulk | null>(null)
  const [modalOpen, setModalOpen] = useState(false)

  const title = show?.name || show?.original_name || ''
  const year = yearOf(show?.first_air_date)
  const plexHref = usePlexHref('show', tmdbId, title, year, !!show?.on_plex)

  if (showQuery.isLoading) return <ShowDetailSkeleton />
  if (showQuery.isError || !show) {
    return <ErrorState message={showQuery.error instanceof Error ? showQuery.error.message : undefined} retryHref="#/tv" />
  }

  async function follow() {
    setSubscribeBusy(true)
    try {
      setSubscription(await createShow(tmdbId))
      queryClient.invalidateQueries({ queryKey: ['shows'] })
      toast({ tone: 'ok', title: `Following ${title}`, body: 'New episodes are picked up on their own.' })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't follow this show", body: errorText(err) })
    } finally {
      setSubscribeBusy(false)
    }
  }
  async function unfollow() {
    if (!subscription) return
    setSubscribeBusy(true)
    try {
      await deleteShow(subscription.id)
      setSubscription(null)
      queryClient.invalidateQueries({ queryKey: ['shows'] })
      toast({ tone: 'info', title: `Unfollowed ${title}`, body: 'Anything already on the way stays in Requests.' })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't stop following", body: errorText(err) })
    } finally {
      setSubscribeBusy(false)
    }
  }

  async function runBulkDownload(scope: 'season' | 'series', seasonNumber: number | null, key: string, redownloadMode?: RedownloadMode) {
    setBulkBusyKey(key)
    try {
      await bulkDownload(tmdbId, {
        scope,
        season_number: seasonNumber,
        redownload_mode: redownloadMode ?? null,
      })
      queryClient.invalidateQueries({ queryKey: ['requests'] })
      queryClient.invalidateQueries({ queryKey: ['episodes', tmdbId] })
      // Re-read: a whole-series add on a returning show also follows it.
      const shows = await listShows()
      setSubscription(shows.find((s) => s.tmdb_id === tmdbId) ?? null)
      queryClient.invalidateQueries({ queryKey: ['shows'] })
      const follows = scope === 'series' && !(show?.status === 'Ended' || show?.status === 'Canceled')
      toast({
        tone: 'info',
        title: `Adding ${title} to Plex`,
        body: `${scope === 'series' ? 'Whole series' : `Season ${seasonNumber}`} · looking for the best copy now${follows ? ' · following for new episodes' : ''}`,
      })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't request that", body: errorText(err) })
    } finally {
      setBulkBusyKey(null)
    }
  }

  function handleBulkClick(scope: 'season' | 'series', seasonNumber: number | null, label: string, key: string) {
    if (show!.on_plex) {
      setPendingBulk({ scope, seasonNumber, label })
      setModalOpen(true)
      return
    }
    setBulkBusyKey(key)
    setRequestModal({ scope, seasonNumber, label })
  }

  const seasons = (show.seasons || []).filter((s) => s.season_number > 0)
  // TMDB calls a limited series "Miniseries" and marks it "Ended" from
  // the day it drops — but not every one is tagged (City of Blood is
  // "Scripted"), so a show that ended after a single season counts too.
  // The page then calls it a limited series and never a season, since the
  // whole thing is one run.
  const limited = show.type === 'Miniseries' || (show.status === 'Ended' && seasons.length === 1)
  const currentSeason = activeSeason ?? (seasons.length ? seasons[seasons.length - 1].season_number : null)
  const currentSeasonLabel = limited ? 'Limited series' : seasons.find((x) => x.season_number === currentSeason)?.name || `Season ${currentSeason}`
  const certification = tvCertificationOf(show, region)
  const genres = (show.genres ?? []).slice(0, 3)
  const network = show.networks?.[0]?.name
  const serviceMark = originalServiceMatch(show, true)


  const pills: DetailPill[] = []
  if (show.vote_average) {
    pills.push({
      star: true,
      text: (
        <>
          {show.vote_average.toFixed(1)}
          {show.vote_count ? <small>· {show.vote_count.toLocaleString()}</small> : null}
        </>
      ),
    })
  }
  if (year) pills.push({ text: year })
  if (certification) pills.push({ text: certification })
  if (limited) pills.push({ text: show.number_of_episodes ? `Limited series · ${show.number_of_episodes} episodes` : 'Limited series' })
  else if (seasons.length) pills.push({ text: `${seasons.length} season${seasons.length === 1 ? '' : 's'}` })
  if (genres.length) pills.push({ text: genreLinks(genres, 'tv'), mute: true })

  const nextEp = show.next_episode_to_air
  const next = nextEp?.air_date
  const nextCode = nextEp?.season_number != null && nextEp?.episode_number != null ? `S${nextEp.season_number}E${nextEp.episode_number}` : null
  // An episode dated today or earlier is out, not upcoming (a season
  // dropping all at once leaves TMDB's "next" pointing at it for a day).
  const nextIsOut = !!next && new Date(`${next}T00:00:00`) <= startOfToday()
  // A finished run wins over whatever stale "next" TMDB still lists.
  const nextLabel = limited
    ? 'Limited series · all out'
    : show.status === 'Ended' || show.status === 'Canceled'
      ? 'No more episodes'
      : next
        ? nextIsOut
          ? ['Out now', nextCode].filter(Boolean).join(' · ')
          : [new Date(`${next}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }), nextCode].filter(Boolean).join(' · ')
        : 'Not scheduled'
  // An ended or cancelled show has nothing left to follow: no Follow
  // button, and the tile shows the years it ran instead.
  const ended = show.status === 'Ended' || show.status === 'Canceled'
  const following = subscription?.status === 'watching'
  const firstYear = show.first_air_date?.slice(0, 4)
  const lastYear = show.last_air_date?.slice(0, 4)
  const sideTiles: DetailTile[] = []
  if (ended) {
    sideTiles.push({
      label: limited ? 'Limited series' : show.status === 'Canceled' ? 'Cancelled' : 'Ended',
      value: firstYear ? `${firstYear}${lastYear && lastYear !== firstYear ? ` – ${lastYear}` : ''}` : '—',
      tone: limited ? 'ice' : 'dim',
    })
  } else {
    sideTiles.push(
      following
        ? { label: 'Following', value: (<><Icon name="check-circle" />Yes</>), tone: 'mint' }
        : { label: 'Following', value: 'No', tone: 'dim' },
    )
  }
  if (limited) sideTiles.push({ label: 'Episodes', value: String(show.number_of_episodes || '—') })
  else sideTiles.push({ label: 'Seasons', value: String(seasons.length || '—') })
  sideTiles.push({ label: 'Next episode', value: nextLabel, tone: next ? 'ice' : 'dim', wide: true })

  // The movie page reads one request's recorded winner; a show has as
  // many as it has episodes and packs, so this is the whole run rolled
  // up server-side. A show downloaded over time genuinely can be mixed
  // (a 1080p season pack, a 2160p one later), and claiming either
  // number would be wrong — hence "Various" rather than picking one.
  const library = show.library
  const fileTiles: DetailTile[] | null =
    library && library.files > 0
      ? [
          { label: 'Quality', value: acrossReleases(library.releases, qualityFromName), tone: 'mint' },
          { label: 'Size', value: library.total_bytes ? formatBytes(library.total_bytes) : '—' },
          { label: 'Episodes', value: String(library.files) },
          { label: 'Source', value: acrossReleases(library.releases, sourceFromName) },
        ]
      : null

  const crew = show.credits?.crew ?? []
  const creators = crew.filter((c) => c.job === 'Creator' || c.job === 'Executive Producer').slice(0, 3)
  const details: DetailRow[] = []
  if (certification) details.push({ label: 'Rated', value: certification })
  if (show.first_air_date) details.push({ label: 'First aired', value: new Date(`${show.first_air_date}T00:00:00`).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) })
  // Always, icon in the corner or not: only the eight curated services
  // have one, and an icon names nothing to anyone who doesn't already
  // recognise it. The rating can live in the corner alone because the
  // chip is its own label; this can't.
  if (network) details.push({ label: 'Network', value: network })
  if (show.status) details.push({ label: 'Status', value: limited ? 'Limited series' : show.status })
  const lang = languageNameOf(show.original_language)
  if (lang) details.push({ label: 'Language', value: lang })
  if (show.genres?.length) details.push({ label: 'Genres', value: genreLinks(show.genres, 'tv', ', ') })
  if (creators.length) details.push({ label: 'Created by', value: peopleLinks(creators) })
  if (show.production_companies?.[0]) details.push({ label: 'Studio', value: show.production_companies[0].name })

  const actions = (
    <>
      {show.on_plex &&
        (plexHref ? (
          <a className="btn pri" href={plexHref} target="_blank" rel="noreferrer">
            <Icon name="play" />
            Play on Plex
          </a>
        ) : (
          <span className="btn pri dis">
            <Icon name="play" />
            Play on Plex
          </span>
        ))}
      {/* One button: Follow, or Unfollow once followed. A show is
          followed only while its row is "watching" — a paused row is the
          anchor a one-off season add leaves behind, not a follow. */}
      {ended ? null : !following ? (
        <button className={`btn ${show.on_plex ? 'sec' : 'pri'}`} disabled={subscribeBusy || show.is_coming_soon} onClick={follow}>
          <Icon name="plus" />
          {show.is_coming_soon ? 'Coming soon' : 'Follow'}
        </button>
      ) : (
        <button className="btn sec" disabled={subscribeBusy} onClick={unfollow}>
          <Icon name="check" />
          Unfollow
        </button>
      )}
      {/* Nothing to add once every aired episode is already on Plex. */}
      {!show.plex_complete && (
        <button className={`btn ${ended && !show.on_plex ? 'pri' : 'sec'}`} disabled={bulkBusyKey === 'series'} onClick={() => handleBulkClick('series', null, 'Complete series', 'series')}>
          <Icon name="download" />
          {bulkBusyKey === 'series' ? 'Adding…' : show.on_plex ? 'Add the rest to Plex' : 'Add all to Plex'}
        </button>
      )}
    </>
  )

  return (
    <>
      <DetailShell
        service={bannerShown ? serviceMark : null}
        certification={bannerShown ? certification : null}
        backdropPath={show.backdrop_path}
        posterPath={show.poster_path}
        logoPath={show.logo_path}
        title={title}
        onPlex={!!show.on_plex}
        pill={showPill(show)}
        pills={pills}
        overview={show.overview}
        actions={actions}
        tiles={sideTiles}
        details={details}
        aside={<DetailCast cast={show.credits?.cast} limit={8} />}
      >
        <DetailTrailer type="tv" tmdbId={tmdbId} backdropPath={show.backdrop_path} />
        {fileTiles && (
          <>
            <h4 className="detail-h4">On disk</h4>
            <FactTiles tiles={fileTiles} />
          </>
        )}
      </DetailShell>

      {seasons.length > 0 && currentSeason != null && (
<section className="detail-section">
        <div className="detail-section-head">
          <h2>
            Episodes <span>{currentSeasonLabel}</span>
          </h2>
        </div>
        {/* A limited series is one run: its one button says so instead of "Season 1". */}
        <div className="detail-season-bar" role="tablist" aria-label="Season">
          {seasons.map((s) => (
            <button key={s.id} role="tab" aria-selected={s.season_number === currentSeason} className={`season-btn${s.season_number === currentSeason ? ' active' : ''}`} onClick={() => setActiveSeason(s.season_number)}>
              {limited ? 'Limited series' : s.name || `Season ${s.season_number}`}
            </button>
          ))}
          <button className="btn sec sm" disabled={bulkBusyKey === `season-${currentSeason}`} onClick={() => handleBulkClick('season', currentSeason, currentSeasonLabel, `season-${currentSeason}`)}>
            <Icon name="download" />
            {bulkBusyKey === `season-${currentSeason}` ? 'Adding…' : limited ? 'Add limited series to Plex' : `Add ${/^season \d/i.test(currentSeasonLabel) ? currentSeasonLabel.toLowerCase() : currentSeasonLabel} to Plex`}
          </button>
        </div>
        <EpisodeList tmdbId={tmdbId} season={currentSeason} />
      </section>
      )}

      <div className="detail-rows">
        <MediaRow title="More" qualifier="like this" items={show.recommendations?.results ?? []} mediaType="tv" />
      </div>

      <RequestModal
        open={requestModal != null}
        title={requestModal?.scope === 'series' ? title : `${title} · ${requestModal?.label ?? ''}`}
        subtitle={requestModal?.scope === 'series' ? (limited ? 'Limited series' : 'Complete series') : 'One season'}
        posterPath={show.poster_path}
        submitLabel="Add to Plex"
        onClose={() => {
          setRequestModal(null)
          setBulkBusyKey(null)
        }}
        onSubmit={() => {
          const pending = requestModal
          setRequestModal(null)
          if (!pending) return
          const key = pending.scope === 'series' ? 'series' : `season-${pending.seasonNumber}`
          runBulkDownload(pending.scope, pending.seasonNumber, key)
        }}
      />
      <RedownloadModal
        open={modalOpen}
        targetLabel={pendingBulk?.label ?? title}
        trackedAvailable={show.on_plex_tracked}
        onClose={() => setModalOpen(false)}
        onChoose={(mode) => {
          setModalOpen(false)
          if (mode === 'reject') return // not offered for shows
          if (pendingBulk) runBulkDownload(pendingBulk.scope, pendingBulk.seasonNumber, pendingBulk.scope === 'series' ? 'series' : `season-${pendingBulk.seasonNumber}`, mode)
        }}
      />
    </>
  )
}
