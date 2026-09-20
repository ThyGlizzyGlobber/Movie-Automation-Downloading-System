import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Icon from '../../components/Icon'
import Img from '../../components/Img'
import { Skel, SkelText } from '../../components/Skeleton'
import '../../components/MediaRow.css'
import { useQuery } from '@tanstack/react-query'
import { listRequests } from '../../api/requests'
import { locateOnPlex } from '../../api/plex'
import { getMovieTrailer } from '../../api/movies'
import { getTvTrailer } from '../../api/tv'
import { backdropUrl } from '../../lib/tmdbImage'
import { profileUrl } from '../../lib/tmdbImage'
import { plexWebUrl } from '../../lib/format'
import { originalServiceMatch } from '../../lib/providers'
import type { CastMember } from '../../types/movies'
import type { RequestOut } from '../../types/requests'
import { DetailH4, type DetailTile } from './DetailShell'
import { browseHref } from '../../api/browse'

export function DetailCast({ cast, limit = 16 }: { cast?: CastMember[]; limit?: number }) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [atStart, setAtStart] = useState(true)
  const top = (cast || []).slice(0, limit)
  if (!top.length) return null
  function scrollByPage(dir: number) {
    const track = trackRef.current
    if (!track) return
    const atEnd = track.scrollLeft >= track.scrollWidth - track.clientWidth - 2
    if (dir > 0 && atEnd) track.scrollTo({ left: 0, behavior: 'smooth' })
    else track.scrollBy({ left: dir * track.clientWidth, behavior: 'smooth' })
  }
  return (
    <>
      <DetailH4>Cast</DetailH4>
      <div className={`hscroll-wrap detail-cast-wrap${atStart ? ' at-start' : ''}`}>
        <div className="detail-cast" ref={trackRef} onScroll={() => setAtStart((trackRef.current?.scrollLeft ?? 0) <= 2)}>
          {top.map((c) => (
            <Link key={c.id} to={`/person/${c.id}`}>
              <Img className="detail-cast-face" src={profileUrl(c.profile_path)} alt="" loading="lazy" />
              <b>{c.name}</b>
              <i>{c.character || ''}</i>
            </Link>
          ))}
        </div>
        <button className="hscroll-arrow hscroll-arrow-left" aria-label="Scroll left" onClick={() => scrollByPage(-1)}>
          <Icon name="back" />
        </button>
        <button className="hscroll-arrow hscroll-arrow-right" aria-label="Scroll right" onClick={() => scrollByPage(1)}>
          <Icon name="next" />
        </button>
      </div>
    </>
  )
}

export function DetailCastSkeleton({ count = 8 }: { count?: number }) {
  return (
    <>
      <DetailH4>Cast</DetailH4>
      <div className="hscroll-wrap detail-cast-wrap" aria-hidden="true">
        <div className="detail-cast">
          {Array.from({ length: count }, (_, i) => (
            <span className="detail-cast-person" key={i}>
              <Skel className="detail-cast-face" />
              <b>
                <SkelText width="76%" center />
              </b>
              <i>
                <SkelText width="52%" center />
              </i>
            </span>
          ))}
        </div>
      </div>
    </>
  )
}

// Genre names as links into the browse page with that genre already
// selected, for the pills ("Action · Comedy") and the facts list.
export function genreLinks(genres: { id: number; name: string }[], type: 'movie' | 'tv', separator = ' · ') {
  return (
    <>
      {genres.map((g, i) => (
        <span key={g.id}>
          {i > 0 && separator}
          <a href={`#${browseHref({ type, genre: g.id })}`}>{g.name}</a>
        </span>
      ))}
    </>
  )
}

// "Name, Name" as person links, for the facts list.
export function peopleLinks(people: { id: number; name: string }[]) {
  return (
    <>
      {people.map((p, i) => (
        <span key={p.id}>
          {i > 0 && ', '}
          <Link to={`/person/${p.id}`}>{p.name}</Link>
        </span>
      ))}
    </>
  )
}

export function FactTiles({ tiles, four = true }: { tiles: DetailTile[]; four?: boolean }) {
  const shown = tiles.filter((t) => t.value !== '' && t.value != null)
  if (!shown.length) return null
  return (
    <div className={`detail-tiles${four ? ' four' : ''}`}>
      {shown.map((t) => (
        <div className={`detail-tile${t.tone ? ` ${t.tone}` : ''}`} key={t.label}>
          <small>{t.label}</small>
          <b>{t.value}</b>
        </div>
      ))}
    </div>
  )
}

// The household's requests for one title (newest first).
export function useTitleRequests(tmdbId: number, kinds: RequestOut['media_type'][]) {
  const query = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), refetchInterval: 8000 })
  const rows = (query.data ?? []).filter((r) => r.tmdb_id === tmdbId && kinds.includes(r.media_type)).sort((a, b) => b.id - a.id)
  return rows
}

// Plex Web link for a title that is in the library, or null.
export function usePlexHref(type: 'movie' | 'show', tmdbId: number, title: string, year: string | number | null | undefined, enabled: boolean) {
  const query = useQuery({
    queryKey: ['plex-locate', type, tmdbId, title, year ?? ''],
    queryFn: () => locateOnPlex(type, title, year, tmdbId),
    enabled: enabled && !!title,
    staleTime: 300_000,
  })
  const d = query.data
  return d && d.available ? plexWebUrl(d.machine_id, d.rating_key) : null
}

const RES_RE = /\b(2160p|4k|uhd|1080p|720p|480p)\b/i
const SRC_RE = /\b(remux|bluray|blu-ray|web-?dl|webrip|web|hdtv|dvdrip)\b/i
const HDR_RE = /\b(dolby ?vision|dv|hdr10\+?|hdr)\b/i

// "4K · HDR" style quality read from a release name.
export function qualityFromName(name: string | null | undefined): string {
  if (!name) return ''
  const flat = name.replace(/[._]/g, ' ')
  const res = flat.match(RES_RE)?.[1]?.toLowerCase()
  const label = res === '2160p' || res === '4k' || res === 'uhd' ? '4K' : res ? res.toLowerCase() : ''
  const hdr = HDR_RE.test(flat) ? 'HDR' : ''
  return [label, hdr].filter(Boolean).join(' ')
}

/** One label for a title that may span several releases: the shared
 *  value when they all agree, "Various" when they don't, an em dash
 *  when nothing parsed. A movie is normally one release and so reads
 *  as itself; a show acquired over time genuinely can be a 1080p
 *  season and a 2160p one, and naming either would be a lie.
 *
 *  Releases a parser can't read (an odd name with no resolution token)
 *  are dropped rather than counted as a difference — one unreadable
 *  name among ten 1080p ones shouldn't turn the tile into "Various"
 *  when the title plainly isn't mixed. */
export function acrossReleases(releases: string[], read: (name: string) => string): string {
  const distinct = [...new Set(releases.map(read).filter(Boolean))]
  if (distinct.length === 0) return '—'
  return distinct.length === 1 ? distinct[0] : 'Various'
}

/** The short "12 Sep" form both detail pages date their files with. */
export function filedOnLabel(addedAt: string | null | undefined): string {
  if (!addedAt) return '—'
  const when = new Date(addedAt)
  return Number.isNaN(when.getTime()) ? '—' : when.toLocaleDateString([], { day: 'numeric', month: 'short' })
}

export function sourceFromName(name: string | null | undefined): string {
  if (!name) return ''
  const m = name.replace(/[._]/g, ' ').match(SRC_RE)?.[1]?.toLowerCase()
  if (!m) return ''
  if (m.startsWith('web')) return m === 'webrip' ? 'WEBRip' : 'WEB-DL'
  if (m.startsWith('blu')) return 'Blu-ray'
  return m.charAt(0).toUpperCase() + m.slice(1)
}

export function serviceTile(item: Parameters<typeof originalServiceMatch>[0], isTv: boolean): DetailTile | null {
  const match = originalServiceMatch(item, isTv)
  if (!match) return null
  return {
    label: 'Service',
    fit: true,
    value: (
      <>
        <img className="detail-tile-icon" src={`/icons/${match.icon}`} alt="" />
        {match.label.replace(' Original', '')}
      </>
    ),
  }
}

// The title's trailer, played inline in the card when one is on file
// (the same self-hosted file the home hero uses).
export function DetailTrailer({ type, tmdbId, backdropPath }: { type: 'movie' | 'tv'; tmdbId: number; backdropPath: string | null | undefined }) {
  const query = useQuery({
    queryKey: ['trailer', type, tmdbId],
    queryFn: () => (type === 'tv' ? getTvTrailer(tmdbId) : getMovieTrailer(tmdbId)),
    staleTime: 600_000,
    retry: false,
  })
  if (query.isLoading) return <DetailTrailerSkeleton />
  const url = query.data?.url
  if (!url) return null
  const poster = backdropUrl(backdropPath) ?? undefined
  return (
    <>
      <DetailH4>Trailer</DetailH4>
      <video className="detail-trailer" src={url} poster={poster} controls playsInline preload="metadata" />
    </>
  )
}

export function DetailTrailerSkeleton() {
  return (
    <>
      <DetailH4>Trailer</DetailH4>
      <Skel className="detail-trailer" />
    </>
  )
}
