import type { ReactNode } from 'react'
import { posterUrl } from '../lib/tmdbImage'
import Img from './Img'
import { genresFor } from '../lib/genres'
import './PosterCard.css'

export interface PosterCardItem {
  id: number
  title?: string | null
  name?: string | null
  original_title?: string | null
  original_name?: string | null
  poster_path?: string | null
  release_date?: string | null
  first_air_date?: string | null
  genre_ids?: number[]
  on_plex?: boolean
}

export function cardTitle(item: PosterCardItem): string {
  return item.title || item.name || item.original_title || item.original_name || 'Untitled'
}

// "2024 · Thriller" (or "2025 · Drama · Series" in a mixed row) under
// the poster, the reference's card caption.
export function cardMeta(item: PosterCardItem, mediaType: 'movie' | 'tv', mixed = false): string {
  const date = item.release_date || item.first_air_date || ''
  const year = date.slice(0, 4)
  const genre = item.genre_ids?.length ? genresFor(mediaType).find((g) => g.id === item.genre_ids![0])?.name : undefined
  return [year, genre, mixed && mediaType === 'tv' ? 'Series' : null].filter(Boolean).join(' · ')
}

export default function PosterCard({
  item,
  mediaType = 'movie',
  caption = true,
  mixed = false,
  posterSrc,
  href,
  chip,
}: {
  item: PosterCardItem
  mediaType?: 'movie' | 'tv'
  /* Title and "year · genre" under the poster (off for the Top 10 row). */
  caption?: boolean
  /* In a row that mixes movies and shows, shows say so in the caption. */
  mixed?: boolean
  /* Artwork from somewhere other than TMDB (a Plex poster). */
  posterSrc?: string | null
  href?: string
  /* A status chip in the poster's top-right corner (the household
     requests row); replaces the On Plex badge when given. */
  chip?: ReactNode
}) {
  const isTv = mediaType === 'tv'
  const title = cardTitle(item)
  const to = href ?? (isTv ? `#/tv/${item.id}` : `#/movies/${item.id}`)
  const meta = caption ? cardMeta(item, mediaType, mixed) : ''
  return (
    <a className="poster-card" href={to}>
      <div className="poster-art">
        <Img src={posterSrc ?? posterUrl(item.poster_path)} alt={caption ? '' : title} loading="lazy" />
        {chip ?? (item.on_plex && <div className="on-plex-badge">On Plex</div>)}
      </div>
      {caption && (
        <div className="poster-caption">
          <b>{title}</b>
          {meta && <small>{meta}</small>}
        </div>
      )}
    </a>
  )
}
