import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listRequests, cancelRequest, clearRequests } from '../../api/requests'
import { getRetention, setRetention } from '../../api/settings'
import { useSession } from '../auth/useSession'
import StatusPill from '../../components/StatusPill'
import ProgressBar from '../../components/ProgressBar'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { posterUrl } from '../../lib/tmdbImage'
import { CANCELLABLE } from '../../lib/status'
import {
  dominantStatus,
  episodeLabelAndHref,
  groupRequestsForDisplay,
  requestLabelAndHref,
  type DisplayItem,
  type SeasonGroup,
  type ShowGroup,
} from '../../lib/requestGrouping'
import { RETENTION_OPTIONS } from '../../lib/retention'
import type { RedownloadMode, RequestOut } from '../../types/requests'
import '../detail/DetailPage.css'
import './RequestsPage.css'

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

function RedownloadTag({ mode }: { mode: RedownloadMode }) {
  return <span className="redownload-tag">Redownload · {mode === 'overwrite' ? 'Overwrite' : 'Upgrade'}</span>
}

// "Cancel" only really makes sense while something's still in progress
// (queued/downloading) — a "complete" request has nothing left to
// cancel, so its action is really "delete the download," shown as a
// plain X instead of the same red text pill. Both call the same
// /api/requests/{id}/cancel route either way (see api.py's own comment
// on why a "complete" cancel still deletes the qBittorrent files).
function RequestCancelAction({ row, onChanged }: { row: RequestOut; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  if (!CANCELLABLE.has(row.status)) return null
  const isDelete = row.status === 'complete'

  async function handleClick() {
    setBusy(true)
    try {
      await cancelRequest(row.id)
      onChanged()
    } catch (err) {
      alert(`Couldn't ${isDelete ? 'delete' : 'cancel'} that: ${err instanceof Error ? err.message : 'Unknown error'}`)
      setBusy(false)
    }
  }

  if (isDelete) {
    return (
      <button className="req-delete-btn" aria-label="Delete" disabled={busy} onClick={handleClick}>
        ✕
      </button>
    )
  }
  return (
    <button className="cancel-btn" disabled={busy} onClick={handleClick}>
      Cancel
    </button>
  )
}

function RequestLeafRow({
  row,
  variant,
  onChanged,
}: {
  row: RequestOut
  variant: 'standalone' | 'child'
  onChanged: () => void
}) {
  const { label, href } = variant === 'standalone' ? requestLabelAndHref(row) : episodeLabelAndHref(row)
  return (
    <div className={`req-row${variant === 'child' ? ' req-child-row' : ''}`}>
      {variant === 'standalone' && <img className="req-poster" src={posterUrl(row.poster_path)} alt="" />}
      <div className="req-main">
        <a className="req-title" href={href}>
          {label}
        </a>
        <div className="req-sub">{formatDate(row.created_at)}</div>
        {row.status === 'downloading' && row.download_progress != null && <ProgressBar progress={row.download_progress} />}
      </div>
      <div className="req-actions">
        {row.redownload_mode && <RedownloadTag mode={row.redownload_mode} />}
        <StatusPill status={row.status} />
        <RequestCancelAction row={row} onChanged={onChanged} />
      </div>
    </div>
  )
}

function SeasonGroupRow({
  tmdbId,
  season,
  expanded,
  onToggle,
  onChanged,
}: {
  tmdbId: number
  season: SeasonGroup
  expanded: boolean
  onToggle: () => void
  onChanged: () => void
}) {
  const children = season.rows
    .slice()
    .sort((a, b) => b.id - a.id)
    .map((r) => <RequestLeafRow key={r.id} row={r} variant="child" onChanged={onChanged} />)
  return (
    <div className="req-group">
      <div className="req-row req-group-header" onClick={onToggle}>
        <button
          className="req-group-toggle-btn"
          aria-label={expanded ? 'Collapse' : 'Expand'}
          onClick={(e) => {
            e.stopPropagation()
            onToggle()
          }}
        >
          {expanded ? '▼' : '▶'}
        </button>
        <div className="req-main">
          <a className="req-title" href={`#/tv/${tmdbId}`} onClick={(e) => e.stopPropagation()}>
            {season.label}
          </a>
          <div className="req-sub">
            {season.rows.length} item{season.rows.length === 1 ? '' : 's'}
          </div>
        </div>
        <div className="req-actions">
          <StatusPill status={dominantStatus(season.rows)} />
        </div>
      </div>
      {expanded && <div className="req-group-children">{children}</div>}
    </div>
  )
}

function ShowGroupRow({
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
    <div className="req-group">
      <div className="req-row req-group-header" onClick={onToggle}>
        <button
          className="req-group-toggle-btn"
          aria-label={expanded ? 'Collapse' : 'Expand'}
          onClick={(e) => {
            e.stopPropagation()
            onToggle()
          }}
        >
          {expanded ? '▼' : '▶'}
        </button>
        <img className="req-poster" src={posterUrl(group.posterPath)} alt="" />
        <div className="req-main">
          <a className="req-title" href={`#/tv/${group.tmdbId}`} onClick={(e) => e.stopPropagation()}>
            {group.title}
          </a>
          <div className="req-sub">
            {group.rows.length} item{group.rows.length === 1 ? '' : 's'}
          </div>
        </div>
        <div className="req-actions">
          <StatusPill status={dominantStatus(group.rows)} />
        </div>
      </div>
      {expanded && (
        <div className="req-group-children">
          {group.seasons.map((season) => {
            const key = `${group.showId}:${season.key}`
            return (
              <SeasonGroupRow
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
  usePageTitle('My Requests')
  useSetHasHero(false)
  const queryClient = useQueryClient()
  const session = useSession()
  const isAdmin = !!session.data?.is_admin

  const requestsQuery = useQuery({
    queryKey: ['requests'],
    queryFn: () => listRequests(),
    refetchInterval: 5000,
  })
  // Retention is a global, admin-only policy (/api/settings/retention is
  // admin-gated) — a regular user just gets the plain "Clear My Requests"
  // button below, no auto-clear picker.
  const retentionQuery = useQuery({ queryKey: ['retention'], queryFn: () => getRetention(), enabled: isAdmin })

  const [expandedShows, setExpandedShows] = useState<Set<number>>(new Set())
  const [expandedSeasons, setExpandedSeasons] = useState<Set<string>>(new Set())
  const [clearing, setClearing] = useState(false)
  const [retentionOpen, setRetentionOpen] = useState(false)

  function onChanged() {
    queryClient.invalidateQueries({ queryKey: ['requests'] })
  }

  function toggleShow(id: number) {
    setExpandedShows((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSeason(key: string) {
    setExpandedSeasons((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  async function handleClear() {
    if (!confirm("Clear finished, cancelled, and failed requests from history? Active downloads won't be affected.")) return
    setClearing(true)
    try {
      await clearRequests()
      onChanged()
    } catch (err) {
      alert(`Couldn't clear requests: ${err instanceof Error ? err.message : 'Unknown error'}`)
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
      alert(`Couldn't update auto-clear: ${err instanceof Error ? err.message : 'Unknown error'}`)
    }
  }

  if (requestsQuery.isLoading) return <LoadingState />
  if (requestsQuery.isError) {
    return <ErrorState message={requestsQuery.error instanceof Error ? requestsQuery.error.message : undefined} retryHref="#/requests" />
  }

  const items: DisplayItem[] = groupRequestsForDisplay(requestsQuery.data ?? [])

  return (
    <>
      <div id="requestsList">
        {items.length === 0 ? (
          <EmptyState message="Nothing requested yet — find a movie or show and tap Add." />
        ) : (
          items.map((item) =>
            item.type === 'standalone' ? (
              // Prefixed: a standalone request's own id and a show's id
              // are both small auto-increment integers from different
              // tables, so the bare numbers collide as React keys once
              // both appear as siblings in this same list.
              <RequestLeafRow key={`req-${item.row.id}`} row={item.row} variant="standalone" onChanged={onChanged} />
            ) : (
              <ShowGroupRow
                key={`show-${item.showId}`}
                group={item}
                expanded={expandedShows.has(item.showId)}
                onToggle={() => toggleShow(item.showId)}
                expandedSeasons={expandedSeasons}
                onToggleSeason={toggleSeason}
                onChanged={onChanged}
              />
            ),
          )
        )}
      </div>

      <div className="clear-requests-fab">
        {retentionOpen && <div className="retention-menu-backdrop" onClick={() => setRetentionOpen(false)} />}
        <div className="split-btn">
          <button className="split-btn-main" disabled={clearing} onClick={handleClear}>
            {clearing ? 'Clearing…' : 'Clear My Requests'}
          </button>
          {isAdmin && (
            <>
              <div className="split-btn-divider" />
              <button className="split-btn-arrow" aria-label="Auto-clear options" onClick={() => setRetentionOpen((o) => !o)}>
                ▾
              </button>
            </>
          )}
        </div>
        {isAdmin && retentionOpen && (
          <div className="retention-menu">
            {RETENTION_OPTIONS.map((o) => (
              <button key={String(o.days)} className="retention-option" onClick={() => handleSetRetention(o.days)}>
                <span>{o.label}</span>
                {(retentionQuery.data?.days ?? null) === o.days && <span className="check">✓</span>}
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
