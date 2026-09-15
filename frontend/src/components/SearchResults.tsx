import { useState, type MouseEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createRequest, listRequests } from '../api/requests'
import { createShow, listShows } from '../api/tv'
import { posterUrl } from '../lib/tmdbImage'
import { NON_TERMINAL, statusMeta } from '../lib/status'
import { hitTitle, hitYear, type SearchHit } from '../lib/liveSearch'
import Icon from './Icon'

// Live results inside the search palette (the reference's search sheet):
// each row carries the action that fits its real state — Request (a
// movie not yet in Plex), Add show (subscribe to a series), or a passive
// Requested / Subscribed / In Plex state. Requests are created exactly
// the way the detail page does it, with the household's default quality;
// anything that needs a choice (a redownload of something already in
// Plex, a specific season) still goes through the detail page, which is
// what tapping the row opens. The search itself lives in lib/liveSearch.

type ActionPhase = 'idle' | 'busy' | 'done' | 'error'

function RowAction({
  hit,
  activeStatus,
  subscribed,
  onDone,
}: {
  hit: SearchHit
  activeStatus: string | null
  subscribed: boolean
  onDone: () => void
}) {
  const [phase, setPhase] = useState<ActionPhase>('idle')

  if (hit.on_plex) {
    return (
      <span className="search-result-state in-plex">
        <Icon name="check-circle" />
        In Plex
      </span>
    )
  }
  if (hit.mediaType === 'tv' && (subscribed || phase === 'done')) {
    return (
      <span className="search-result-state pending">
        <Icon name="check" />
        Subscribed
      </span>
    )
  }
  if (hit.mediaType === 'movie' && (activeStatus || phase === 'done')) {
    const meta = activeStatus ? statusMeta(activeStatus) : null
    return (
      <span className="search-result-state pending">
        <Icon name={meta?.icon ?? 'clock'} />
        {meta?.label ?? 'Requested'}
      </span>
    )
  }

  async function act(e: MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    setPhase('busy')
    try {
      if (hit.mediaType === 'tv') await createShow(hit.id)
      else await createRequest({ tmdb_id: hit.id, query: hitTitle(hit) })
      setPhase('done')
      onDone()
    } catch {
      setPhase('error')
    }
  }

  return (
    <button className={`search-result-action${phase === 'error' ? ' error' : ''}`} disabled={phase === 'busy'} onClick={act}>
      <Icon name={phase === 'error' ? 'alert-circle' : phase === 'busy' ? 'loader' : 'plus'} />
      {phase === 'error' ? 'Try again' : phase === 'busy' ? 'Adding…' : hit.mediaType === 'tv' ? 'Add show' : 'Request'}
    </button>
  )
}

export default function SearchResults({
  hits,
  highlighted,
  onOpen,
  onHover,
}: {
  hits: SearchHit[]
  highlighted: number
  onOpen: (hit: SearchHit) => void
  onHover: (index: number) => void
}) {
  const queryClient = useQueryClient()
  // Active movie requests and show subscriptions, so a title the
  // household already asked for shows its state instead of a second
  // Request button. Shared query keys with the Requests page / show
  // pages, so an action here refreshes those too.
  const requests = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), staleTime: 15_000 })
  const shows = useQuery({ queryKey: ['shows'], queryFn: () => listShows(), staleTime: 15_000 })

  const activeByMovie = new Map<number, string>()
  for (const r of requests.data ?? []) {
    if (r.media_type === 'movie' && NON_TERMINAL.has(r.status)) activeByMovie.set(r.tmdb_id, r.status)
  }
  const subscribedShows = new Set((shows.data ?? []).map((s) => s.tmdb_id))

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['requests'] })
    queryClient.invalidateQueries({ queryKey: ['shows'] })
  }

  return (
    <div className="search-results" role="listbox" aria-label="Search results">
      {hits.map((hit, i) => (
        <div
          key={`${hit.mediaType}-${hit.id}`}
          role="option"
          aria-selected={i === highlighted}
          className={`search-result${i === highlighted ? ' on' : ''}`}
          onMouseEnter={() => onHover(i)}
          onClick={() => onOpen(hit)}
        >
          <img className="search-result-poster" src={posterUrl(hit.poster_path)} alt="" loading="lazy" />
          <div className="search-result-text">
            <div className="search-result-title">{hitTitle(hit)}</div>
            <div className="search-result-meta">
              {hit.mediaType === 'tv' ? 'Series' : 'Movie'}
              {hitYear(hit) && ` · ${hitYear(hit)}`}
            </div>
          </div>
          {!!hit.vote_average && (
            <span className="search-result-rating">
              <Icon name="star" />
              {hit.vote_average.toFixed(1)}
            </span>
          )}
          <RowAction
            hit={hit}
            activeStatus={hit.mediaType === 'movie' ? (activeByMovie.get(hit.id) ?? null) : null}
            subscribed={subscribedShows.has(hit.id)}
            onDone={refresh}
          />
        </div>
      ))}
    </div>
  )
}
