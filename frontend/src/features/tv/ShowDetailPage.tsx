import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getTvShow, listShows, createShow, deleteShow, bulkDownload } from '../../api/tv'
import RequestModal from '../../components/RequestModal'
import EpisodeList from './EpisodeList'
import MediaRow from '../../components/MediaRow'
import RedownloadModal from '../../components/RedownloadModal'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import Icon from '../../components/Icon'
import DetailShell, { type DetailPill, type DetailRow, type DetailTile } from '../detail/DetailShell'
import { DetailCast, DetailTrailer, peopleLinks, usePlexHref, useTitleRequests } from '../detail/DetailBits'
import { latestRequestLabel } from '../../lib/requestGrouping'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { errorText, useToast } from '../../lib/toast'
import { languageNameOf, tvCertificationOf, yearOf } from '../../lib/detailHelpers'
import { tvHeroBadge } from '../../lib/homeHero'
import type { ShowOut } from '../../types/shows'
import type { RedownloadMode } from '../../types/requests'
import '../detail/DetailPage.css'

interface PendingBulk {
  scope: 'season' | 'series'
  seasonNumber: number | null
  label: string
}

export default function ShowDetailPage() {
  const { id } = useParams()
  const tmdbId = Number(id)
  useSetHasHero(true)
  const queryClient = useQueryClient()
  const { toast } = useToast()

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
  const plexHref = usePlexHref('show', title, year, !!show?.on_plex)
  const requests = useTitleRequests(tmdbId, ['episode', 'pack'])

  if (showQuery.isLoading) return <LoadingState />
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

  async function runBulkDownload(scope: 'season' | 'series', seasonNumber: number | null, key: string, redownloadMode?: RedownloadMode, notify?: boolean) {
    setBulkBusyKey(key)
    try {
      await bulkDownload(tmdbId, {
        scope,
        season_number: seasonNumber,
        redownload_mode: redownloadMode ?? null,
        notify: notify ?? null,
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
  const currentSeason = activeSeason ?? (seasons.length ? seasons[seasons.length - 1].season_number : null)
  const currentSeasonLabel = seasons.find((x) => x.season_number === currentSeason)?.name || `Season ${currentSeason}`
  const certification = tvCertificationOf(show)
  const genres = (show.genres ?? []).slice(0, 3).map((g) => g.name).join(' · ')
  const badge = tvHeroBadge(show)
  const network = show.networks?.[0]?.name

  const eyebrow = show.is_coming_soon
    ? { text: 'Coming soon', tone: 'amber' as const }
    : { text: [show.status === 'Returning Series' ? 'Returning series' : show.status, badge].filter(Boolean).join(' · ') || 'Series', tone: (show.on_plex ? 'mint' : 'ice') as 'mint' | 'ice' }

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
  if (seasons.length) pills.push({ text: `${seasons.length} season${seasons.length === 1 ? '' : 's'}` })
  if (genres) pills.push({ text: genres, mute: true })

  const nextEp = show.next_episode_to_air
  const next = nextEp?.air_date
  const nextLabel = next
    ? [
        new Date(`${next}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }),
        nextEp?.season_number != null && nextEp?.episode_number != null ? `S${nextEp.season_number}E${nextEp.episode_number}` : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : show.status === 'Ended' || show.status === 'Canceled'
      ? 'No more episodes'
      : 'Not scheduled'
  // An ended or cancelled show has nothing left to follow: no Follow
  // button, and the tile shows the years it ran instead.
  const ended = show.status === 'Ended' || show.status === 'Canceled'
  const following = subscription?.status === 'watching'
  const firstYear = show.first_air_date?.slice(0, 4)
  const lastYear = show.last_air_date?.slice(0, 4)
  const sideTiles: DetailTile[] = []
  if (ended) {
    sideTiles.push({ label: show.status === 'Canceled' ? 'Cancelled' : 'Ended', value: firstYear ? `${firstYear}${lastYear && lastYear !== firstYear ? ` – ${lastYear}` : ''}` : '—', tone: 'dim' })
  } else {
    sideTiles.push(
      following
        ? { label: 'Following', value: (<><Icon name="check-circle" />Yes</>), tone: 'mint' }
        : { label: 'Following', value: 'No', tone: 'dim' },
    )
  }
  sideTiles.push({ label: 'Seasons', value: String(seasons.length || '—') })
  sideTiles.push({ label: 'Next episode', value: nextLabel, tone: next ? 'ice' : 'dim', wide: true })

  const crew = show.credits?.crew ?? []
  const creators = crew.filter((c) => c.job === 'Creator' || c.job === 'Executive Producer').slice(0, 3)
  const details: DetailRow[] = []
  if (certification) details.push({ label: 'Rated', value: certification })
  if (show.first_air_date) details.push({ label: 'First aired', value: new Date(`${show.first_air_date}T00:00:00`).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) })
  if (network) details.push({ label: 'Network', value: network })
  if (show.status) details.push({ label: 'Status', value: show.status })
  const lang = languageNameOf(show.original_language)
  if (lang) details.push({ label: 'Language', value: lang })
  if (show.genres?.length) details.push({ label: 'Genres', value: show.genres.map((g) => g.name).join(', ') })
  if (creators.length) details.push({ label: 'Created by', value: peopleLinks(creators) })
  if (show.production_companies?.[0]) details.push({ label: 'Studio', value: show.production_companies[0].name })

  const actions = (
    <>
      {show.on_plex &&
        (plexHref ? (
          <a className="btn pri" href={plexHref} target="_blank" rel="noreferrer">
            <Icon name="play" />
            Play
          </a>
        ) : (
          <span className="btn pri dis">
            <Icon name="play" />
            Play
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
      <button className={`btn ${ended && !show.on_plex ? 'pri' : 'sec'}`} disabled={bulkBusyKey === 'series'} onClick={() => handleBulkClick('series', null, 'Complete series', 'series')}>
        <Icon name="download" />
        {bulkBusyKey === 'series' ? 'Adding…' : 'Add all to Plex'}
      </button>
    </>
  )

  return (
    <>
      <DetailShell
        backdropPath={show.backdrop_path}
        posterPath={show.poster_path}
        logoPath={show.logo_path}
        title={title}
        onPlex={!!show.on_plex}
        eyebrow={eyebrow.text}
        eyebrowTone={eyebrow.tone}
        pills={pills}
        overview={show.overview}
        actions={actions}
        tiles={sideTiles}
        details={details}
        requests={requests}
        requestLabel={latestRequestLabel}
        aside={<DetailCast cast={show.credits?.cast} limit={8} />}
      >
        <DetailTrailer type="tv" tmdbId={tmdbId} backdropPath={show.backdrop_path} />
      </DetailShell>

      {seasons.length > 0 && currentSeason != null && (
<section className="detail-section">
        <div className="detail-section-head">
          <h2>
            Episodes <span>{currentSeasonLabel}</span>
          </h2>
        </div>
        <div className="detail-season-bar" role="tablist" aria-label="Season">
          {seasons.map((s) => (
            <button key={s.id} role="tab" aria-selected={s.season_number === currentSeason} className={`season-btn${s.season_number === currentSeason ? ' active' : ''}`} onClick={() => setActiveSeason(s.season_number)}>
              {s.name || `Season ${s.season_number}`}
            </button>
          ))}
          <button className="btn sec sm" disabled={bulkBusyKey === `season-${currentSeason}`} onClick={() => handleBulkClick('season', currentSeason, currentSeasonLabel, `season-${currentSeason}`)}>
            <Icon name="download" />
            {bulkBusyKey === `season-${currentSeason}` ? 'Adding…' : `Add ${/^season \d/i.test(currentSeasonLabel) ? currentSeasonLabel.toLowerCase() : currentSeasonLabel} to Plex`}
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
        subtitle={requestModal?.scope === 'series' ? 'Complete series' : 'One season'}
        posterPath={show.poster_path}
        submitLabel="Add to Plex"
        onClose={() => {
          setRequestModal(null)
          setBulkBusyKey(null)
        }}
        onSubmit={(notify) => {
          const pending = requestModal
          setRequestModal(null)
          if (!pending) return
          const key = pending.scope === 'series' ? 'series' : `season-${pending.seasonNumber}`
          runBulkDownload(pending.scope, pending.seasonNumber, key, undefined, notify)
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
