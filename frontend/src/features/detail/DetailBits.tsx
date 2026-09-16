import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listRequests } from '../../api/requests'
import { locateOnPlex } from '../../api/plex'
import { profileUrl } from '../../lib/tmdbImage'
import { plexWebUrl } from '../../lib/format'
import { originalServiceMatch } from '../../lib/providers'
import type { CastMember } from '../../types/movies'
import type { RequestOut } from '../../types/requests'
import { DetailH4, type DetailTile } from './DetailShell'

export function DetailCast({ cast }: { cast?: CastMember[] }) {
  const top = (cast || []).slice(0, 12)
  if (!top.length) return null
  return (
    <>
      <DetailH4>Cast</DetailH4>
      <div className="detail-cast">
        {top.map((c) => (
          <Link key={c.id} to={`/person/${c.id}`}>
            <img src={profileUrl(c.profile_path)} alt="" loading="lazy" />
            <b>{c.name}</b>
            <i>{c.character || ''}</i>
          </Link>
        ))}
      </div>
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
export function usePlexHref(type: 'movie' | 'show', title: string, year: string | number | null | undefined, enabled: boolean) {
  const query = useQuery({
    queryKey: ['plex-locate', type, title, year ?? ''],
    queryFn: () => locateOnPlex(type, title, year),
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
    value: (
      <>
        <img className="detail-tile-icon" src={`/icons/${match.icon}`} alt="" />
        {match.label.replace(' Original', '')}
      </>
    ),
  }
}
