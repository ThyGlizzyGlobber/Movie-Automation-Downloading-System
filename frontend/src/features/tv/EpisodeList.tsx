import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getSeasonEpisodes, requestEpisode } from '../../api/tv'
import { stillUrl } from '../../lib/tmdbImage'
import { statusMeta } from '../../lib/status'
import { REGION_TIMEZONE } from '../../lib/regions'
import { useCertificationRegion } from '../auth/useSession'
import { errorText, useToast } from '../../lib/toast'
import Icon from '../../components/ui/Icon'
import Img from '../../components/ui/Img'
import { Skel, SkelText } from '../../components/ui/Skeleton'
// Pills drawn here rather than through StatusPill, so its styles come in by hand.
import '../../components/ui/StatusPill.css'
import type { EpisodeStatus } from '../../types/features'

// One season's episodes with what the household has of each (the
// reference's episode rows): in Plex, requested (live status), failed,
// unaired, or missing — the last with a per-episode Request button.

/** "Airs today | 12:00 pm" while the release moment is still ahead,
 *  null once it has passed. TVmaze gives the moment in UTC; it's shown
 *  on the household's clock (Settings › Region), not whichever one the
 *  viewing device happens to be set to — the same calendar every other
 *  date in the app is on. */
export function airsLater(airsAt: string | null | undefined, region: string, now = new Date()): string | null {
  if (!airsAt) return null
  const at = new Date(airsAt)
  if (Number.isNaN(at.getTime()) || at <= now) return null
  const timeZone = REGION_TIMEZONE[region.toUpperCase()]
  const dayOf = (d: Date) => d.toLocaleDateString('en-CA', { timeZone })
  const time = at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', timeZone })
  const day = dayOf(at) === dayOf(now) ? 'today' : at.toLocaleDateString(undefined, { weekday: 'short', timeZone })
  return `Airs ${day} | ${time}`
}

function EpisodePill({ ep }: { ep: EpisodeStatus }) {
  const region = useCertificationRegion()
  if (ep.state === 'in_plex') {
    return (
      <span className="status-pill status-complete">
        <Icon name="check-circle" className="status-icon" />
        On Plex
      </span>
    )
  }
  if (ep.state === 'requested' || ep.state === 'failed') {
    const meta = statusMeta(ep.status ?? 'queued')
    const pct = ep.status === 'downloading' && ep.download_progress != null ? ` | ${Math.round(ep.download_progress * 100)}%` : ''
    return (
      <span className={`status-pill ${meta.cls}`}>
        <Icon name={meta.icon} className="status-icon" />
        {meta.label.replace(/…$/, '')}
        {pct}
      </span>
    )
  }
  if (ep.state === 'unaired') {
    return <span className="status-pill status-queued episode-pill-muted">Not yet aired</span>
  }
  if (ep.state === 'holding') {
    // Held either side of its release: due later today, or out but
    // deliberately not searched for yet — see the air buffer in
    // Settings › TV scheduling. Only the exact release time can tell
    // the two apart; without one it reads as the latter, as before.
    const due = airsLater(ep.airs_at, region)
    if (due) return <span className="status-pill status-queued episode-pill-muted">{due}</span>
    return <span className="status-pill status-queued episode-pill-muted">Waiting for a good copy</span>
  }
  return <span className="status-pill status-queued episode-pill-muted">Not requested</span>
}

function EpisodeAction({ tmdbId, season, ep, onDone }: { tmdbId: number; season: number; ep: EpisodeStatus; onDone: () => void }) {
  const [phase, setPhase] = useState<'idle' | 'busy' | 'error'>('idle')
  const { toast } = useToast()
  if (ep.state !== 'missing' && ep.state !== 'failed') return null
  async function act() {
    setPhase('busy')
    try {
      await requestEpisode(tmdbId, season, ep.episode_number)
      onDone()
      setPhase('idle')
      toast({ tone: 'info', title: `Adding S${String(season).padStart(2, '0')}E${String(ep.episode_number).padStart(2, '0')} to Plex`, body: ep.name || 'Looking for the best copy now' })
    } catch (err) {
      setPhase('error')
      toast({ tone: 'error', title: "Couldn't request that episode", body: errorText(err) })
    }
  }
  return (
    <button className={`episode-request-btn${phase === 'error' ? ' error' : ''}`} disabled={phase === 'busy'} onClick={act}>
      <Icon name={phase === 'busy' ? 'loader' : phase === 'error' ? 'alert-circle' : ep.state === 'failed' ? 'refresh' : 'plus'} />
      {phase === 'busy' ? 'Adding…' : phase === 'error' ? 'Try again' : ep.state === 'failed' ? 'Retry' : 'Add to Plex'}
    </button>
  )
}

export default function EpisodeList({ tmdbId, season }: { tmdbId: number; season: number }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['tv-episodes', tmdbId, season],
    queryFn: () => getSeasonEpisodes(tmdbId, season),
    // Keep polling while anything in this season is in flight.
    refetchInterval: (q) => (q.state.data?.episodes.some((e) => e.state === 'requested') ? 5000 : false),
  })
  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['tv-episodes', tmdbId, season] })
    queryClient.invalidateQueries({ queryKey: ['requests'] })
  }
  if (query.isLoading) return <EpisodeListSkeleton />
  if (query.isError || !query.data) return <div className="episode-list-note">Couldn't load this season's episodes.</div>
  const { episodes, in_plex, aired } = query.data
  if (!episodes.length) return <div className="episode-list-note">No episodes listed for this season yet.</div>
  const pct = aired ? Math.round((in_plex / aired) * 100) : 0
  return (
    <div className="episode-list">
      <div className="episode-progress">
        <span>
          {in_plex} of {aired} on Plex
        </span>
        <i>
          <b style={{ width: `${pct}%` }} />
        </i>
      </div>
      <div className="episode-grid">
        {episodes.map((ep) => (
          <div className={`episode-card episode-${ep.state}`} key={ep.episode_number} title={ep.overview || undefined}>
            <div className="episode-still">
              {ep.still_path ? <Img src={stillUrl(ep.still_path)} alt="" loading="lazy" /> : <span className="episode-still-empty" />}
              <span className="episode-num">{String(ep.episode_number).padStart(2, '0')}</span>
              <span className="episode-chip">
                <EpisodePill ep={ep} />
              </span>
            </div>
            <div className="episode-text">
              <b>{ep.name || `Episode ${ep.episode_number}`}</b>
              <small>{ep.runtime ? `${ep.runtime} min` : ep.air_date ? ep.air_date.slice(0, 4) : ''}</small>
            </div>
            <div className="episode-action">
              <EpisodeAction tmdbId={tmdbId} season={season} ep={ep} onDone={refresh} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function EpisodeListSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div className="episode-list" aria-hidden="true">
      <div className="episode-progress">
        <SkelText inline width={96} />
        <i />
      </div>
      <div className="episode-grid">
        {Array.from({ length: count }, (_, i) => (
          <div className="episode-card" key={i}>
            <Skel className="episode-still" />
            <div className="episode-text">
              <b>
                <SkelText width="72%" />
              </b>
              <small>
                <SkelText width="34%" />
              </small>
            </div>
            <div className="episode-action">
              <Skel className="episode-request-btn">
                <Icon name="plus" />
                Add to Plex
              </Skel>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* The show page's Episodes section while the show itself loads. */
export function EpisodesSectionSkeleton() {
  return (
    <section className="detail-section" aria-busy="true">
      <div className="detail-section-head">
        <h2>
          Episodes <SkelText inline width={96} />
        </h2>
      </div>
      <div className="detail-season-bar" aria-hidden="true">
        <div className="detail-season-tabs">
          <Skel className="season-btn">Season 1</Skel>
          <Skel className="season-btn">Season 2</Skel>
        </div>
        <Skel className="btn sec sm">
          <Icon name="download" />
          Add season 2 to Plex
        </Skel>
      </div>
      <EpisodeListSkeleton />
    </section>
  )
}
