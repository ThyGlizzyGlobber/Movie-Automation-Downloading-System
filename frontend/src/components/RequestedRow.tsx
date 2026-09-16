import { useQuery } from '@tanstack/react-query'
import { listRequests } from '../api/requests'
import { statusMeta } from '../lib/status'
import type { RequestOut } from '../types/requests'
import MediaRow from './MediaRow'
import PosterCard from './PosterCard'
import Icon from './Icon'

const STATUS_TONE: Record<string, string> = {
  downloading: 'var(--status-downloading)',
  queued: 'var(--text)',
  searching: 'var(--status-searching)',
  failed: 'var(--status-failed)',
  complete: 'var(--status-complete)',
  'downloaded, not filed': 'var(--status-complete)',
  'no qualifying results': 'var(--status-nomatch)',
  'insufficient free space': 'var(--status-nospace)',
}

// "Requested by the household": the newest request per title as a
// poster row with a status chip (progress while downloading).
export default function RequestedRow() {
  const query = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), refetchInterval: 5000 })
  const rows = query.data ?? []
  const byTitle = new Map<string, RequestOut>()
  for (const r of rows) {
    if (r.status === 'cancelled') continue
    const key = `${r.media_type === 'movie' ? 'movie' : 'tv'}:${r.tmdb_id}`
    const prev = byTitle.get(key)
    if (!prev || r.id > prev.id) byTitle.set(key, r)
  }
  const items = [...byTitle.values()].sort((a, b) => b.id - a.id).slice(0, 20)
  if (!items.length) return null
  return (
    <MediaRow
      title="On the way"
      qualifier="for the household"
      items={items}
      mediaType={(r) => (r.media_type === 'movie' ? 'movie' : 'tv')}
      expandHref="/requests"
      seeAllLabel="All requests"
      renderItem={(r) => {
        const meta = statusMeta(r.status)
        const isTv = r.media_type !== 'movie'
        const label = r.status === 'downloading' && r.download_progress != null ? `${Math.round(r.download_progress * 100)}%` : meta.label
        return (
          <PosterCard
            item={{ id: r.tmdb_id, title: r.title, release_date: r.release_year ? `${r.release_year}-01-01` : null, poster_path: r.poster_path }}
            mediaType={isTv ? 'tv' : 'movie'}
            mixed
            chip={
              <span className="poster-chip" style={{ color: STATUS_TONE[r.status] ?? 'var(--text-dim)' }}>
                <Icon name={meta.icon} />
                {label}
              </span>
            }
          />
        )
      }}
    />
  )
}
