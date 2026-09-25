import { useQuery } from '@tanstack/react-query'
import { getOnDeck } from '../api/plex'
import { plexWebUrl } from '../lib/format'
import type { OnDeckItem } from '../types/features'
import MediaRow from './MediaRow'
import Icon from './Icon'
import { Skel, SkelText } from './Skeleton'
import './ContinueWatchingRow.css'

// Plex's Continue Watching, on Home (the reference's row). Landscape
// cards with a progress line; the play chip opens the title in Plex Web,
// the card itself opens this app's detail page when Plex knows the TMDB
// id (it usually does), otherwise Plex.

function subtitle(item: OnDeckItem): string {
  const parts: string[] = []
  if (item.type === 'episode' && item.season_number != null && item.episode_number != null) {
    parts.push(`S${item.season_number} E${item.episode_number}`)
  } else if (item.year) {
    parts.push(String(item.year))
  }
  if (item.remaining_minutes != null) parts.push(`${item.remaining_minutes} min left`)
  return parts.join(' | ')
}

export function ContinueWatchingSkeleton() {
  return (
    <MediaRow
      className="continue-row"
      title="Continue"
      qualifier="watching"
      items={[]}
      mediaType="movie"
      loading
      skeletonCount={6}
      renderSkeleton={() => (
        <div className="continue-card">
          <Skel className="continue-art" />
          <b>
            <SkelText width="70%" />
          </b>
          <small>
            <SkelText width="46%" />
          </small>
        </div>
      )}
    />
  )
}

export default function ContinueWatchingRow() {
  const query = useQuery({ queryKey: ['plex-on-deck'], queryFn: getOnDeck, staleTime: 60_000, refetchInterval: 120_000 })
  const data = query.data
  if (query.isLoading) return <ContinueWatchingSkeleton />
  if (!data || !data.available || !data.items.length) return null
  const machineId = data.machine_id
  const items = data.items.map((it) => ({ ...it, id: Number(it.rating_key) }))
  return (
    <MediaRow
      className="continue-row"
      title="Continue"
      qualifier="watching"
      items={items}
      mediaType={(it) => it.media_type}
      renderItem={(item) => {
        const plexHref = plexWebUrl(machineId, item.rating_key)
        const detailHref = item.tmdb_id ? (item.media_type === 'tv' ? `#/tv/${item.tmdb_id}` : `#/movies/${item.tmdb_id}`) : plexHref
        const title = item.type === 'episode' ? item.show_title || item.title || '' : item.title || ''
        return (
          <div className="continue-card">
            <a className="continue-art" href={detailHref} target={item.tmdb_id ? undefined : '_blank'} rel="noreferrer">
              {item.art_url ? <img src={item.art_url} alt="" loading="lazy" /> : null}
              <span className="continue-progress">
                <i style={{ width: `${Math.round(item.progress * 100)}%` }} />
              </span>
            </a>
            <a className="continue-play" href={plexHref} target="_blank" rel="noreferrer" aria-label={`Play ${title} on Plex`}>
              <Icon name="play" />
            </a>
            <b>{title}</b>
            <small>{item.type === 'episode' && item.title ? `${subtitle(item)} | ${item.title}` : subtitle(item)}</small>
          </div>
        )
      }}
    />
  )
}
