import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listRequests, cancelRequest, clearRequests } from '../../api/requests'
import { getRetention, setRetention } from '../../api/settings'
import { useSession } from '../auth/useSession'
import StatusPill from '../../components/StatusPill'
import StatusRing from '../../components/StatusRing'
import Icon from '../../components/Icon'
import Img from '../../components/Img'
import { Skel, SkelText, SkelWords } from '../../components/Skeleton'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle } from '../../lib/chrome'
import { posterUrl } from '../../lib/tmdbImage'
import { relativeTime } from '../../lib/format'
import { CANCELLABLE, NON_TERMINAL, statusDetail, statusMeta } from '../../lib/status'
import {
  dominantStatus,
  groupRequestsForDisplay,
  packScopeLabel,
  requestLabelAndHref,
  type DisplayItem,
  type SeasonGroup,
  type ShowGroup,
} from '../../lib/requestGrouping'
import { RETENTION_OPTIONS } from '../../lib/retention'
import { errorText, useToast } from '../../lib/toast'
import type { RequestOut } from '../../types/requests'
import './RequestsPage.css'

type Filter = 'all' | 'active' | 'plex' | 'failed'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'active', label: 'Active' },
  { id: 'plex', label: 'On Plex' },
  { id: 'failed', label: 'Failed' },
]
const FAILED_STATES = new Set(['failed', 'no qualifying results', 'insufficient free space'])

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

// One card per title. Movies are a single line; a show's card expands
// into its seasons, each a strip of small episode pills, so a long-
// running show never turns into a wall of nested rows.
function Row({
  poster,
  title,
  href,
  meta,
  status,
  progress,
  actions,
  onToggle,
  expanded,
  children,
}: {
  poster?: string | null
  title: string
  href: string
  meta: string
  status: string
  progress: number | null
  actions?: ReactNode
  onToggle?: () => void
  expanded?: boolean
  children?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const clickable = !!onToggle
  return (
    <div className={`rq-card${expanded ? ' rq-expanded' : ''}`}>
      <div
        className={`rq-row${clickable ? ' rq-clickable' : ''}${open ? ' rq-open' : ''}`}
        onClick={() => (onToggle ? onToggle() : setOpen((o) => !o))}
      >
        <div className="rq-art">
          <Img src={posterUrl(poster ?? null)} alt="" />
        </div>
        <div className="rq-t">
          <a className="rq-title" href={href} onClick={(e) => e.stopPropagation()}>
            {title}
          </a>
          <small>{meta}</small>
          <span className="rq-pill-m">
            <StatusPill status={status} />
          </span>
        </div>
        <div className="rq-state">
          <StatusPill status={status} />
        </div>
        <div className="rq-prog">
          <StatusRing status={status} progress={progress} size={44} />
        </div>
        <div className="rq-acts" onClick={(e) => e.stopPropagation()}>
          {actions}
          {onToggle && (
            <button className={`rq-circ${expanded ? ' on' : ''}`} aria-label={expanded ? 'Collapse' : 'Expand'} onClick={onToggle}>
              <Icon name="next" />
            </button>
          )}
        </div>
      </div>
      {expanded && children}
    </div>
  )
}

// A request row while the list loads: poster, title, meta line, the
// status pill and ring, and the action button.
const SKELETON_ROWS = 6

function StatusPillSkeleton() {
  return (
    <Skel className="status-pill">
      <Icon name="check-circle" className="status-icon" />
      On Plex
    </Skel>
  )
}

function RowSkeleton() {
  return (
    <div className="rq-card" aria-hidden="true">
      <div className="rq-row">
        <div className="rq-art">
          <Skel className="rq-art-skel" />
        </div>
        <div className="rq-t">
          <span className="rq-title">
            <SkelText width="46%" />
          </span>
          <small>
            <SkelText width="62%" />
          </small>
          <span className="rq-pill-m">
            <StatusPillSkeleton />
          </span>
        </div>
        <div className="rq-state">
          <StatusPillSkeleton />
        </div>
        <div className="rq-prog">
          <Skel className="rq-ring-skel" />
        </div>
        <div className="rq-acts">
          <Skel className="rq-circ" />
        </div>
      </div>
    </div>
  )
}

// Cancel while something is still in flight; delete the file once it
// has finished. Both go through the same cancel route.
function CancelAction({ row, onChanged, compact = false }: { row: RequestOut; onChanged: () => void; compact?: boolean }) {
  const [busy, setBusy] = useState(false)
  const { toast } = useToast()
  if (!CANCELLABLE.has(row.status)) return null
  const isDelete = row.status === 'complete'
  if (compact && isDelete) return null
  async function handleClick() {
    if (isDelete && !confirm(`Delete ${row.title} from Plex?`)) return
    setBusy(true)
    try {
      await cancelRequest(row.id)
      onChanged()
    } catch (err) {
      toast({ tone: 'error', title: `Couldn't ${isDelete ? 'delete' : 'cancel'} that`, body: errorText(err) })
      setBusy(false)
    }
  }
  if (compact) {
    return (
      <button className="rq-ep-x" aria-label="Cancel" disabled={busy} onClick={handleClick}>
        <Icon name="close" />
      </button>
    )
  }
  return (
    <button className={`rq-circ${isDelete ? ' danger' : ''}`} aria-label={isDelete ? 'Delete' : 'Cancel'} disabled={busy} onClick={handleClick}>
      <Icon name={isDelete ? 'trash' : 'close'} />
    </button>
  )
}

function leafMeta(r: RequestOut): string {
  const bits: string[] = []
  if (r.media_type === 'movie') {
    if (r.release_year) bits.push(String(r.release_year))
    bits.push('Movie')
  } else if (r.media_type === 'episode') {
    bits.push(`S${String(r.season_number).padStart(2, '0')}E${String(r.episode_number).padStart(2, '0')}`)
  } else {
    bits.push(packScopeLabel(r))
  }
  if (r.requested_by_username) bits.push(r.requested_by_username)
  if (r.redownload_mode) bits.push(r.redownload_mode === 'overwrite' ? 'Replacing' : 'Upgrading')
  bits.push(relativeTime(r.updated_at))
  if (FAILED_STATES.has(r.status)) bits.push(statusDetail(r.status, r.download_progress, r.error_message))
  return bits.join(' · ')
}

function LeafRow({ row, onChanged }: { row: RequestOut; onChanged: () => void }) {
  const { href } = requestLabelAndHref(row)
  return (
    <Row
      poster={row.poster_path}
      title={row.title}
      href={href}
      meta={leafMeta(row)}
      status={row.status}
      progress={row.download_progress}
      actions={<CancelAction row={row} onChanged={onChanged} />}
    />
  )
}

function episodePillLabel(r: RequestOut): string {
  if (r.media_type === 'episode') return `E${String(r.episode_number).padStart(2, '0')}`
  return r.season_number == null ? 'Whole series' : 'Season pack'
}

// A season inside a show's card: a heading with its count and a strip
// of pills, one per episode (or pack), coloured by state.
function SeasonStrip({ season, onChanged }: { season: SeasonGroup; onChanged: () => void }) {
  const rows = season.rows.slice().sort((a, b) => {
    if (a.media_type !== b.media_type) return a.media_type === 'pack' ? -1 : 1
    return (a.episode_number ?? 0) - (b.episode_number ?? 0)
  })
  const ready = rows.filter((r) => r.status === 'complete').length
  return (
    <div className="rq-season">
      <div className="rq-season-head">
        <b>{season.label}</b>
        <small>
          {ready} of {rows.length} on Plex
        </small>
      </div>
      <div className="rq-eps">
        {rows.map((r) => {
          const meta = statusMeta(r.status)
          const pct = r.status === 'downloading' && r.download_progress != null ? ` ${Math.round(r.download_progress * 100)}%` : ''
          return (
            <span key={r.id} className={`rq-ep ${meta.cls}`} title={`${episodePillLabel(r)} · ${meta.label} · ${statusDetail(r.status, r.download_progress, r.error_message)}`}>
              <Icon name={meta.icon} />
              {episodePillLabel(r)}
              {pct}
              <CancelAction row={r} onChanged={onChanged} compact />
            </span>
          )
        })}
      </div>
    </div>
  )
}

// A show's own progress: the average over everything it has on the way
// or done — a finished season counts as 100%, a downloading one as its
// own percentage, one still waiting or searching as 0%. Failed and
// cancelled rows don't count. Null when nothing is moving or done.
function groupProgress(rows: RequestOut[]): number | null {
  const counted = rows.filter((r) => NON_TERMINAL.has(r.status) || r.status === 'complete' || r.status === 'downloaded, not filed')
  if (!counted.length) return null
  const total = counted.reduce((sum, r) => {
    if (r.status === 'complete' || r.status === 'downloaded, not filed') return sum + 1
    if (r.status === 'downloading') return sum + (r.download_progress ?? 0)
    return sum
  }, 0)
  return total / counted.length
}

function ShowRow({ group, expanded, onToggle, onChanged }: { group: ShowGroup; expanded: boolean; onToggle: () => void; onChanged: () => void }) {
  const ready = group.rows.filter((r) => r.status === 'complete').length
  const active = group.rows.filter((r) => NON_TERMINAL.has(r.status)).length
  const status = dominantStatus(group.rows)
  // When the show as a whole reads as failed, say why: the newest failed
  // row's own explanation (a pack that stepped aside, a floor miss…).
  const explained = FAILED_STATES.has(status) ? group.rows.filter((r) => FAILED_STATES.has(r.status)).sort((a, b) => b.id - a.id)[0] : null
  const meta = [
    'Series',
    `${group.rows.length} item${group.rows.length === 1 ? '' : 's'}`,
    `${ready} on Plex`,
    active ? `${active} on the way` : null,
    relativeTime(group.rows.reduce((best, r) => (r.updated_at > best ? r.updated_at : best), group.rows[0].updated_at)),
    explained ? statusDetail(explained.status, explained.download_progress, explained.error_message) : null,
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <Row
      poster={group.posterPath}
      title={group.title}
      href={`#/tv/${group.tmdbId}`}
      meta={meta}
      status={status}
      progress={groupProgress(group.rows)}
      expanded={expanded}
      onToggle={onToggle}
    >
      <div className="rq-seasons">
        {group.seasons.map((season) => (
          <SeasonStrip key={season.key} season={season} onChanged={onChanged} />
        ))}
      </div>
    </Row>
  )
}

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
  const [expandedShows, setExpandedShows] = useState<Set<number>>(new Set())
  const [clearing, setClearing] = useState(false)
  const [retentionOpen, setRetentionOpen] = useState(false)

  function onChanged() {
    queryClient.invalidateQueries({ queryKey: ['requests'] })
  }
  function toggleIn<T>(set: Set<T>, key: T): Set<T> {
    const next = new Set(set)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
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
  const items = allItems.filter((it) => matchesFilter(it.type === 'standalone' ? it.row.status : dominantStatus(it.rows), filter))
  const people = new Set(rows.map((r) => r.requested_by_username).filter(Boolean)).size
  const summary = loading
    ? null
    : rows.length
    ? `${rows.length} request${rows.length === 1 ? '' : 's'}${people > 1 ? ` from ${people} people` : ''} · updated ${relativeTime(new Date(requestsQuery.dataUpdatedAt).toISOString())}`
    : 'Nothing on the way yet'

  return (
    <div className="rq">
      <div className="rq-head">
        <div>
          <h1 className="rq-h1">Requests</h1>
          <p className="rq-lead">
            {summary ?? <SkelWords text="13 requests · updated just now" />}
            {rows.length > 0 && (downloading > 0 || queued > 0) && (
              <span className="rq-lead-m">
                {' · '}
                {[downloading > 0 ? `${downloading} downloading` : null, queued > 0 ? `${queued} queued` : null].filter(Boolean).join(' · ')}
              </span>
            )}
          </p>
        </div>
        <div className="rq-stats">
          <div className="rq-stat">
            <span className="rq-stat-icon ice">
              <Icon name="download" />
            </span>
            <div>
              <b>{loading ? <Skel>0</Skel> : downloading}</b>
              <small>Downloading</small>
            </div>
          </div>
          <div className="rq-stat">
            <span className="rq-stat-icon">
              <Icon name="clock" />
            </span>
            <div>
              <b>{loading ? <Skel>0</Skel> : queued}</b>
              <small>Queued</small>
            </div>
          </div>
          <div className="rq-stat">
            <span className="rq-stat-icon mint">
              <Icon name="plex" />
            </span>
            <div>
              <b>{loading ? <Skel>0</Skel> : addedToday}</b>
              <small>Added today</small>
            </div>
          </div>
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

      <div className="rq-list" aria-busy={loading || undefined}>
        {loading ? (
          Array.from({ length: SKELETON_ROWS }, (_, i) => <RowSkeleton key={i} />)
        ) : items.length === 0 ? (
          <EmptyState
            icon="download"
            title={rows.length ? 'Nothing here' : 'Nothing queued'}
            message={rows.length ? 'Nothing matches that filter.' : 'Find a movie or show and tap Request. It shows up here while it downloads.'}
            action={
              rows.length ? undefined : (
                <a className="retry" href="#/browse?type=movie">
                  <Icon name="search" />
                  Browse
                </a>
              )
            }
          />
        ) : (
          items.map((item) =>
            item.type === 'standalone' ? (
              <LeafRow key={`req-${item.row.id}`} row={item.row} onChanged={onChanged} />
            ) : (
              <ShowRow
                key={`show-${item.showId}`}
                group={item}
                expanded={expandedShows.has(item.showId)}
                onToggle={() => setExpandedShows((s) => toggleIn(s, item.showId))}
                onChanged={onChanged}
              />
            ),
          )
        )}
      </div>
    </div>
  )
}
