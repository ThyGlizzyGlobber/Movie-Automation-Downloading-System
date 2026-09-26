import { Link } from 'react-router-dom'
import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listRequests, clearRequests } from '../../api/requests'
import { getRetention, setRetention } from '../../api/settings'
import { useSession } from '../auth/useSession'
import Icon from '../../components/Icon'
import PosterCard from '../../components/PosterCard'
import DownloadBar from '../../components/DownloadBar'
import RequestStatusChip from '../../components/RequestStatusChip'
import { PosterCardSkeleton, Skel, SkelWords } from '../../components/Skeleton'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle } from '../../lib/chrome'
import { relativeTime } from '../../lib/format'
import { FAILED_STATES, NON_TERMINAL, statusDetail } from '../../lib/status'
import { dominantStatus, groupProgress, groupRequestsForDisplay, packScopeLabel, requestLabelAndHref, type DisplayItem } from '../../lib/requestGrouping'
import { RETENTION_OPTIONS } from '../../lib/retention'
import { errorText, useToast } from '../../lib/toast'
import type { RequestOut } from '../../types/requests'
import RequestSheet from './RequestSheet'
import './RequestsPage.css'

type Filter = 'all' | 'active' | 'plex' | 'failed'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'active', label: 'Active' },
  { id: 'plex', label: 'On Plex' },
  { id: 'failed', label: 'Failed' },
]

function matchesFilter(status: string, filter: Filter): boolean {
  if (filter === 'all') return true
  if (filter === 'active') return NON_TERMINAL.has(status)
  if (filter === 'plex') return status === 'complete' || status === 'downloaded, not filed'
  return FAILED_STATES.has(status)
}

function isToday(iso: string): boolean {
  const d = new Date(iso)
  const now = new Date()
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
}

function itemKey(item: DisplayItem): string {
  return item.type === 'standalone' ? `req-${item.row.id}` : `show-${item.showId}`
}

function itemStatus(item: DisplayItem): string {
  return item.type === 'standalone' ? item.row.status : dominantStatus(item.rows)
}

// Everything a card draws, for a movie (or a stray episode with no show
// to sit under) and for a whole show alike.
interface CardModel {
  tmdbId: number
  isTv: boolean
  title: string
  poster: string | null
  href: string
  status: string
  progress: number | null
  meta: string
  hint: string
}

function leafMeta(r: RequestOut): string {
  if (FAILED_STATES.has(r.status) || r.status === 'downloaded, not filed') return statusDetail(r.status, r.download_progress, r.error_message)
  const scope =
    r.media_type === 'movie'
      ? r.release_year
        ? String(r.release_year)
        : 'Movie'
      : r.media_type === 'episode'
      ? `S${String(r.season_number).padStart(2, '0')}E${String(r.episode_number).padStart(2, '0')}`
      : packScopeLabel(r)
  return [scope, r.requested_by_username ?? (r.media_type === 'movie' ? 'Movie' : 'Series')].join(' | ')
}

function cardModel(item: DisplayItem): CardModel {
  if (item.type === 'standalone') {
    const r = item.row
    return {
      tmdbId: r.tmdb_id,
      isTv: r.media_type !== 'movie',
      title: r.title,
      poster: r.poster_path,
      href: requestLabelAndHref(r).href,
      status: r.status,
      progress: r.download_progress,
      meta: leafMeta(r),
      hint: statusDetail(r.status, r.download_progress, r.error_message),
    }
  }
  const status = dominantStatus(item.rows)
  const ready = item.rows.filter((r) => r.status === 'complete').length
  const explained = FAILED_STATES.has(status) ? item.rows.filter((r) => FAILED_STATES.has(r.status)).sort((a, b) => b.id - a.id)[0] : null
  const reason = explained ? statusDetail(explained.status, explained.download_progress, explained.error_message) : null
  return {
    tmdbId: item.tmdbId,
    isTv: true,
    title: item.title,
    poster: item.posterPath,
    href: `#/tv/${item.tmdbId}`,
    status,
    progress: groupProgress(item.rows),
    meta: reason ?? `Series | ${ready} of ${item.rows.length} on Plex`,
    hint: reason ?? `${ready} of ${item.rows.length} on Plex`,
  }
}

// A request as the browse grids draw a title: the poster (linking to its
// page) with a status chip, then the title. A download in progress puts
// its bar straight under the poster; everything else keeps a one-line
// caption. The ⋯ opens the sheet with the rest.
function RequestCard({ item, onOpen }: { item: DisplayItem; onOpen: () => void }) {
  const m = cardModel(item)
  const downloading = m.status === 'downloading'
  return (
    <div className={`rq-card${m.status === 'cancelled' ? ' rq-dim' : ''}${downloading ? ' rq-live' : ''}`}>
      <PosterCard
        item={{ id: m.tmdbId, title: m.title, poster_path: m.poster }}
        mediaType={m.isTv ? 'tv' : 'movie'}
        caption={false}
        href={m.href}
        chip={<RequestStatusChip status={m.status} />}
      />
      {downloading && <DownloadBar progress={m.progress} className="rq-card-bar" />}
      <div className="rq-cap">
        <div className="rq-cap-text">
          {/* Link, not an anchor to "#/…" — see PosterCard for why the
              difference decides where the next page starts scrolled. */}
          <Link className="rq-cap-title" to={m.href.replace(/^#/, '')}>
            {m.title}
          </Link>
          {!downloading && <small title={m.hint}>{m.meta}</small>}
        </div>
        <button className="rq-more" aria-label={`Details for ${m.title}`} onClick={onOpen}>
          <Icon name="more" />
        </button>
      </div>
    </div>
  )
}

const SKELETON_CARDS = 14

export default function RequestsPage() {
  usePageTitle('Requests')
  const queryClient = useQueryClient()
  const session = useSession()
  const isAdmin = !!session.data?.is_admin
  const { toast } = useToast()

  const requestsQuery = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), refetchInterval: 5000 })
  // Auto-clear is an admin-only policy; everyone else just gets Clear.
  const retentionQuery = useQuery({ queryKey: ['retention'], queryFn: () => getRetention(), enabled: isAdmin })

  const [filter, setFilter] = useState<Filter>('all')
  // The sheet follows its title by key, so the 5s poll keeps it live and
  // it closes on its own if the title is cleared away underneath it.
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const [retentionOpen, setRetentionOpen] = useState(false)
  const closeSheet = useCallback(() => setOpenKey(null), [])

  function onChanged() {
    queryClient.invalidateQueries({ queryKey: ['requests'] })
  }

  async function handleClear() {
    if (!confirm('Clear finished, cancelled and failed requests? Downloads in progress stay.')) return
    setClearing(true)
    try {
      await clearRequests()
      onChanged()
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't clear requests", body: errorText(err) })
    } finally {
      setClearing(false)
    }
  }

  async function handleSetRetention(days: number | null) {
    setRetentionOpen(false)
    try {
      await setRetention(days)
      queryClient.invalidateQueries({ queryKey: ['retention'] })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't change auto-clear", body: errorText(err) })
    }
  }

  const loading = requestsQuery.isLoading
  if (requestsQuery.isError) {
    return <ErrorState message={requestsQuery.error instanceof Error ? requestsQuery.error.message : undefined} retryHref="#/requests" />
  }

  const rows = requestsQuery.data ?? []
  const downloading = rows.filter((r) => r.status === 'downloading').length
  const queued = rows.filter((r) => r.status === 'queued' || r.status === 'searching').length
  const addedToday = rows.filter((r) => r.status === 'complete' && isToday(r.updated_at)).length
  const allItems: DisplayItem[] = groupRequestsForDisplay(rows)
  const items = allItems.filter((it) => matchesFilter(itemStatus(it), filter))
  const openItem = openKey ? allItems.find((it) => itemKey(it) === openKey) : undefined

  const lead = rows.length
    ? [
        downloading ? `${downloading} downloading` : null,
        queued ? `${queued} queued` : null,
        addedToday ? `${addedToday} added today` : null,
        `updated ${relativeTime(new Date(requestsQuery.dataUpdatedAt).toISOString())}`,
      ]
        .filter(Boolean)
        .join(' | ')
    : 'Nothing on the way yet'

  return (
    <div className="rq">
      <div className="rq-head">
        <div>
          <h1 className="rq-h1">
            Requests
            {allItems.length > 0 && <span className="rq-count"> | {allItems.length}</span>}
          </h1>
          <p className="rq-lead">{loading ? <SkelWords text="2 downloading | 3 queued | updated just now" /> : lead}</p>
        </div>
      </div>

      <div className="rq-toolbar">
        <div className="seg" role="tablist" aria-label="Filter requests">
          {FILTERS.map((f) => (
            <button key={f.id} role="tab" aria-selected={filter === f.id} className={filter === f.id ? 'active' : ''} onClick={() => setFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
        {loading ? (
          <div className="rq-clear" aria-hidden="true">
            <Skel className="rq-clear-btn">
              <Icon name="trash" />
              Clear finished
            </Skel>
            {isAdmin && <Skel className="rq-circ" />}
          </div>
        ) : rows.length > 0 && (
          <div className="rq-clear">
            {retentionOpen && <div className="retention-menu-backdrop" onClick={() => setRetentionOpen(false)} />}
            <button className="rq-clear-btn" disabled={clearing} onClick={handleClear}>
              <Icon name="trash" />
              {clearing ? 'Clearing…' : 'Clear finished'}
            </button>
            {isAdmin && (
              <button className="rq-circ" aria-label="Auto-clear options" onClick={() => setRetentionOpen((o) => !o)}>
                <Icon name="more" />
              </button>
            )}
            {isAdmin && retentionOpen && (
              <div className="retention-menu">
                <div className="retention-menu-title">Clear automatically after</div>
                {RETENTION_OPTIONS.map((o) => (
                  <button key={String(o.days)} className="retention-option" onClick={() => handleSetRetention(o.days)}>
                    <span>{o.label}</span>
                    {(retentionQuery.data?.days ?? null) === o.days && <Icon name="check" className="check" />}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {loading ? (
        <div className="grid category-grid" aria-busy="true">
          {Array.from({ length: SKELETON_CARDS }, (_, i) => (
            <PosterCardSkeleton key={i} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon="download"
          title={rows.length ? 'Nothing here' : 'Nothing queued'}
          message={rows.length ? 'Nothing matches that filter.' : 'Find a movie or show and tap Request. It shows up here while it downloads.'}
          action={
            rows.length ? undefined : (
              <Link className="retry" to="/browse?type=movie">
                <Icon name="search" />
                Browse
              </Link>
            )
          }
        />
      ) : (
        <div className="grid category-grid">
          {items.map((item) => (
            <RequestCard key={itemKey(item)} item={item} onOpen={() => setOpenKey(itemKey(item))} />
          ))}
        </div>
      )}

      {openItem && <RequestSheet item={openItem} onClose={closeSheet} onChanged={onChanged} />}
    </div>
  )
}
