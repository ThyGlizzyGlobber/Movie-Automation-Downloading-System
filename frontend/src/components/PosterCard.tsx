import { posterUrl } from '../lib/tmdbImage'
import './PosterCard.css'

export interface PosterCardItem {
  id: number
  title?: string | null
  name?: string | null
  original_title?: string | null
  original_name?: string | null
  poster_path?: string | null
  on_plex?: boolean
}

export default function PosterCard({
  item,
  mediaType = 'movie',
}: {
  item: PosterCardItem
  mediaType?: 'movie' | 'tv'
}) {
  const isTv = mediaType === 'tv'
  const title = item.title || item.name || item.original_title || item.original_name || 'Untitled'
  const href = isTv ? `#/tv/${item.id}` : `#/movies/${item.id}`
  return (
    <a className="poster-card" href={href}>
      <div className="poster-art">
        <img src={posterUrl(item.poster_path)} alt="" loading="lazy" />
        {item.on_plex && <div className="on-plex-badge">On Plex</div>}
      </div>
      <div className="cap">{title}</div>
    </a>
  )
}
