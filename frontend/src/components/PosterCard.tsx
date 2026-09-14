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
        {/* No caption underneath — the poster art itself always carries
            the title, so a repeated text label below it is redundant
            (per direct request). alt carries the accessible name instead,
            since there's no longer any visible text to serve that role. */}
        <img src={posterUrl(item.poster_path)} alt={title} loading="lazy" />
        {item.on_plex && <div className="on-plex-badge">On Plex</div>}
      </div>
    </a>
  )
}
