import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getSeasonEpisodes, requestEpisode } from '../../api/tv'
import { stillUrl } from '../../lib/tmdbImage'
import { statusMeta } from '../../lib/status'
import { errorText, useToast } from '../../lib/toast'
import Icon from '../../components/Icon'
import type { EpisodeStatus } from '../../types/features'

// One season's episodes with what the household has of each (the
// reference's episode rows): in Plex, requested (live status), failed,
// unaired, or missing — the last with a per-episode Request button.

function EpisodePill({ ep }: { ep: EpisodeStatus }) {
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
    const pct = ep.status === 'downloading' && ep.download_progress != null ? ` · ${Math.round(ep.download_progress * 100)}%` : ''
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
  if (query.isLoading) return <div className="episode-list-note">Loading episodes…</div>
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
              {ep.still_path ? <img src={stillUrl(ep.still_path)} alt="" loading="lazy" /> : <span className="episode-still-empty" />}
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
