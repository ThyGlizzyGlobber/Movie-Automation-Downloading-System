import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listRequests, cancelRequest, clearRequests } from '../../api/requests'
import { getRetention, setRetention } from '../../api/settings'
import { useSession } from '../auth/useSession'
import StatusPill from '../../components/StatusPill'
import StatusRing from '../../components/StatusRing'
import Icon from '../../components/Icon'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { posterUrl } from '../../lib/tmdbImage'
import { relativeTime } from '../../lib/format'
import { CANCELLABLE, NON_TERMINAL, statusDetail } from '../../lib/status'
import {
  dominantStatus,
  episodeLabelAndHref,
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
  { id: 'plex', label: 'In Plex' },
  { id: 'failed', label: 'Failed' },
]
const FAILED_STATES = new Set(['failed', 'no qualifying results', 'insufficient free space'])

function matchesFilter(status: string, filter: Filter): boolean {
  if (filter === 'all') return true
  if (filter === 'active') return NON_TERMINAL.has(status)
  if (filter === 'plex') return status === 'complete' || status === 'downloaded, not filed'
  return FAILED_STATES.has(status)
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

function isToday(iso: string): boolean {
  const d = new Date(iso)
  const now = new Date()
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
}

// The reference's request row: poster, title and a one-line description,
// the state pill with a plain sentence under it, a ring with the timing,
// and circle actions. Groups (a show, then its seasons) use the same row
// with a chevron where the poster would be.
function RequestRow({
  poster,
  chevron,
  title,
  href,
  meta,
  status,
  detail,
  detailTitle,
  progress,
  updatedAt,
  createdAt,
  actions,
  depth = 0,
  onToggle,
  expanded,
}: {
  poster?: string | null
  chevron?: boolean
  title: string
  href: string
  meta: string
  status: string
  detail: string
  detailTitle?: string
  progress: number | null
  updatedAt: string
  createdAt: string
  actions?: ReactNode
  depth?: number
  onToggle?: () => void
  expanded?: boolean
}) {
  const [open, setOpen] = useState(false)
  const clickable = !!onToggle
  return (
    <div
      className={`rq-row depth-${depth}${clickable ? ' rq-clickable' : ''}${open ? ' rq-open' : ''}`}
      onClick={() => (onToggle ? onToggle() : setOpen((o) => !o))}
    >
      <div className="rq-art">
        {poster !== undefined ? (
          <img src={posterUrl(poster)} alt="" />
        ) : chevron ? (
          <span className={`rq-chev${expanded ? ' on' : ''}`}>
            <Icon name="next" />
          </span>
        ) : null}
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
        <div className="rq-detail" title={detailTitle}>
          {detail}
        </div>
      </div>
      <div className="rq-prog">
        <StatusRing status={status} progress={progress} />
        <div className="rq-k">
          {relativeTime(updatedAt)}
          <small>Requested {formatDate(createdAt)}</small>
        </div>
      </div>
      <div className="rq-acts" onClick={(e) => e.stopPropagation()}>
        {chevron && onToggle && (
          <button className={`rq-circ${expanded ? ' on' : ''}`} aria-label={expanded ? 'Collapse' : 'Expand'} onClick={onToggle}>
            <Icon name="next" />
          </button>
        )}
        {actions}
      </div>
    </div>
  )
}

// Cancel while something is still in flight; delete the file once it
// has finished. Both go through the same cancel route.
function CancelAction({ row, onChanged }: { row: RequestOut; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const { toast } = useToast()
  if (!CANCELLABLE.has(row.status)) return null
  const isDelete = row.status === 'complete'
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
  return (
    <button className={`rq-circ${isDelete ? ' danger' : ''}`} aria-label={isDelete ? 'Delete' : 'Cancel'} disabled={busy} onClick={handleClick}>
      <Icon name={isDelete ? 'trash' : 'close'} />
    </button>
  )
}

function rowMeta(r: RequestOut): string {
  const bits: string[] = []
  if (r.media_type === 'movie') {
    if (r.release_year) bits.push(String(r.release_year))
    bits.push('Movie')
  } else if (r.media_type === 'episode') {
    bits.push('Episode')
  } else {
    bits.push(packScopeLabel(r))
  }
  if (r.requested_by_username) bits.push(r.requested_by_username)
  if (r.redownload_mode) bits.push(r.redownload_mode === 'overwrite' ? 'Replacing' : 'Upgrading')
  return bits.join(' · ')
}

function LeafRow({ row, variant, onChanged, depth }: { row: RequestOut; variant: 'standalone' | 'child'; onChanged: () => void; depth: number }) {
  const { label, href } = variant === 'standalone' ? requestLabelAndHref(row) : episodeLabelAndHref(row)
  return (
    <RequestRow
      poster={variant === 'standalone' ? row.poster_path : undefined}
      title={label}
      href={href}
      meta={rowMeta(row)}
      status={row.status}
      detail={statusDetail(row.status, row.download_progress)}
      detailTitle={row.status === 'failed' && row.error_message ? row.error_message : undefined}
      progress={row.download_progress}
      updatedAt={row.updated_at}
      createdAt={row.created_at}
      depth={depth}
      actions={<CancelAction row={row} onChanged={onChanged} />}
    />
  )
}

function groupDetail(rows: RequestOut[]): string {
  const ready = rows.filter((r) => r.status === 'complete').length
  const active = rows.filter((r) => NON_TERMINAL.has(r.status)).length
  const bits = [`${ready} in Plex`]
  if (active) bits.push(`${active} on the way`)
  return bits.join(' · ')
}

function latest(rows: RequestOut[], key: 'updated_at' | 'created_at'): string {
  return rows.reduce((best, r) => (r[key] > best ? r[key] : best), rows[0][key])
}

function SeasonRow({ tmdbId, season, expanded, onToggle, onChanged }: { tmdbId: number; season: SeasonGroup; expanded: boolean; onToggle: () => void; onChanged: () => void }) {
  return (
    <div className="rq-group">
      <RequestRow
        chevron
        title={season.label}
        href={`#/tv/${tmdbId}`}
        meta={`${season.rows.length} item${season.rows.length === 1 ? '' : 's'}`}
        status={dominantStatus(season.rows)}
        detail={groupDetail(season.rows)}
        progress={null}
        updatedAt={latest(season.rows, 'updated_at')}
        createdAt={latest(season.rows, 'created_at')}
        depth={1}
        expanded={expanded}
        onToggle={onToggle}
      />
      {expanded && (
        <div className="rq-children">
          {season.rows
            .slice()
            .sort((a, b) => b.id - a.id)
            .map((r) => (
              <LeafRow key={r.id} row={r} variant="child" onChanged={onChanged} depth={2} />
            ))}
        </div>
      )}
    </div>
  )
}

function ShowRow({
  group,
  expanded,
  onToggle,
  expandedSeasons,
  onToggleSeason,
  onChanged,
}: {
  group: ShowGroup
  expanded: boolean
  onToggle: () => void
  expandedSeasons: Set<string>
  onToggleSeason: (key: string) => void
  onChanged: () => void
}) {
  return (
    <div className="rq-group">
      <RequestRow
        poster={group.posterPath}
        chevron
        title={group.title}
        href={`#/tv/${group.tmdbId}`}
        meta={`Series · ${group.rows.length} item${group.rows.length === 1 ? '' : 's'}`}
        status={dominantStatus(group.rows)}
        detail={groupDetail(group.rows)}
        progress={null}
        updatedAt={latest(group.rows, 'updated_at')}
        createdAt={latest(group.rows, 'created_at')}
        expanded={expanded}
        onToggle={onToggle}
      />
      {expanded && (
        <div className="rq-children">
          {group.seasons.map((season) => {
            const key = `${group.showId}:${season.key}`
            return (
              <SeasonRow
                key={key}
                tmdbId={group.tmdbId}
                season={season}
                expanded={expandedSeasons.has(key)}
                onToggle={() => onToggleSeason(key)}
                onChanged={onChanged}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function RequestsPage() {
  usePageTitle('Requests')
  useSetHasHero(false)
  const queryClient = useQueryClient()
  const session = useSession()
  const isAdmin = !!session.data?.is_admin
  const { toast } = useToast()

  const requestsQuery = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), refetchInterval: 5000 })
  // Auto-clear is an admin-only policy; everyone else just gets Clear.
  const retentionQuery = useQuery({ queryKey: ['retention'], queryFn: () => getRetention(), enabled: isAdmin })

  const [filter, setFilter] = useState<Filter>('all')
  const [expandedShows, setExpandedShows] = useState<Set<number>>(new Set())
  const [expandedSeasons, setExpandedSeasons] = useState<Set<string>>(new Set())
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

  if (requestsQuery.isLoading) return <LoadingState />
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
  const summary = rows.length
    ? `${rows.length} request${rows.length === 1 ? '' : 's'}${people > 1 ? ` from ${people} people` : ''} · updated ${relativeTime(new Date(requestsQuery.dataUpdatedAt).toISOString())}`
    : 'Nothing on the way yet'

  return (
    <div className="rq">
      <div className="rq-head">
        <div>
          <h1 className="rq-h1">Requests</h1>
          <p className="rq-lead">
            {summary}
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
              <b>{downloading}</b>
              <small>Downloading</small>
            </div>
          </div>
          <div className="rq-stat">
            <span className="rq-stat-icon">
              <Icon name="clock" />
            </span>
            <div>
              <b>{queued}</b>
              <small>Queued</small>
            </div>
          </div>
          <div className="rq-stat">
            <span className="rq-stat-icon mint">
              <Icon name="plex" />
            </span>
            <div>
              <b>{addedToday}</b>
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
        {rows.length > 0 && (
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

      <div className="rq-list">
        {items.length === 0 ? (
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
              <LeafRow key={`req-${item.row.id}`} row={item.row} variant="standalone" onChanged={onChanged} depth={0} />
            ) : (
              <ShowRow
                key={`show-${item.showId}`}
                group={item}
                expanded={expandedShows.has(item.showId)}
                onToggle={() => setExpandedShows((s) => toggleIn(s, item.showId))}
                expandedSeasons={expandedSeasons}
                onToggleSeason={(key) => setExpandedSeasons((s) => toggleIn(s, key))}
                onChanged={onChanged}
              />
            ),
          )
        )}
      </div>
    </div>
  )
}
