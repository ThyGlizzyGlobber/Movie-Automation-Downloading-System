import { Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getPipelineSettings, getStorageDetails, setPipelineSettings } from '../../api/settings'
import { listHousehold } from '../../api/admin'
import { listRequests } from '../../api/requests'
import { getRecentlyAdded } from '../../api/plex'
import Avatar from '../../components/Avatar'
import Icon from '../../components/Icon'
import PosterCard from '../../components/PosterCard'
import Img from '../../components/Img'
import { PosterCardSkeleton, Skel, SkelText, SkelWords } from '../../components/Skeleton'
import StatusPill from '../../components/StatusPill'
import { formatBytes, plexWebUrl, relativeTime } from '../../lib/format'
import { posterUrl } from '../../lib/tmdbImage'
import { NON_TERMINAL, statusDetail } from '../../lib/status'
import { requestLabelAndHref } from '../../lib/requestGrouping'
import type { RequestOut } from '../../types/requests'
import { RESOLUTION_OPTIONS } from './PipelinePanel'
import './DashboardPanel.css'

// Settings opens here: the household at a glance. Four numbers, the
// library drive, who is downloading what right now, what Plex added
// lately, and the two Downloads settings people reach for.

function Stat({ icon, tone, value, label, loading = false }: { icon: Parameters<typeof Icon>[0]['name']; tone: string; value: string | number; label: string; loading?: boolean }) {
  return (
    <div className="dash-stat">
      <span className={`dash-stat-icon ${tone}`}>
        <Icon name={icon} />
      </span>
      <div>
        <b>{loading ? <Skel>0</Skel> : value}</b>
        <small>{label}</small>
      </div>
    </div>
  )
}

function ActiveRow({ r, hasAvatar }: { r: RequestOut; hasAvatar: boolean }) {
  const { href } = requestLabelAndHref(r)
  const pct = r.status === 'downloading' && r.download_progress != null ? Math.round(r.download_progress * 100) : null
  const who = r.requested_by_username || 'Obsidian'
  return (
    <Link className="dash-active" to={href.replace(/^#/, '')}>
      <span className="dash-active-who" title={who}>
        {r.requested_by_plex_id ? (
          <Avatar src={`/api/admin/users/${encodeURIComponent(r.requested_by_plex_id)}/avatar`} name={who} hasPicture={hasAvatar} />
        ) : (
          <Icon name="gear" />
        )}
      </span>
      <Img className="dash-active-poster" src={posterUrl(r.poster_path)} alt="" />
      <span className="dash-active-text">
        <b>{r.title}</b>
        <small>
          {who}
          {r.media_type === 'episode' && r.season_number != null ? ` | S${String(r.season_number).padStart(2, '0')}E${String(r.episode_number).padStart(2, '0')}` : ''}
          {r.media_type === 'pack' ? (r.season_number == null ? ' | Complete series' : ` | Season ${r.season_number}`) : ''}
          {' | '}
          {statusDetail(r.status, r.download_progress)}
        </small>
        <i className="dash-active-bar">
          <b style={{ width: `${pct ?? (r.status === 'downloading' ? 2 : 0)}%` }} />
        </i>
      </span>
      <StatusPill status={r.status} />
    </Link>
  )
}

// Loading placeholders for the dashboard's cards, in each card's own
// markup.
function ActiveRowSkeleton() {
  return (
    <div className="dash-active" aria-hidden="true">
      <Skel className="dash-active-who" />
      <Skel className="dash-active-poster" />
      <span className="dash-active-text">
        <b>
          <SkelText width="48%" />
        </b>
        <small>
          <SkelText width="64%" />
        </small>
        <i className="dash-active-bar" />
      </span>
      <Skel className="status-pill">
        <Icon name="download" className="status-icon" />
        Downloading
      </Skel>
    </div>
  )
}

function StorageSkeleton() {
  return (
    <div aria-hidden="true">
      <div className="dash-storage-line">
        <b>
          <Skel>1.2 TB</Skel>
        </b>
        <span>
          <SkelWords text="of 3.6 TB used | 33%" />
        </span>
      </div>
      <Skel className="dash-storage-bar" />
      <ul className="dash-storage-legend">
        {['Movies', 'TV shows', 'Other', 'Free'].map((label) => (
          <li key={label}>
            <SkelText width="80%" />
          </li>
        ))}
      </ul>
    </div>
  )
}

function QuickSettings() {
  const queryClient = useQueryClient()
  const pipeline = useQuery({ queryKey: ['settings', 'pipeline'], queryFn: getPipelineSettings })
  const [floor, setFloor] = useState<string | null>(null)
  const [maxSize, setMaxSize] = useState<string | null>(null)
  const [flash, setFlash] = useState<'saved' | 'error' | null>(null)
  useEffect(() => {
    if (!pipeline.data) return
    setFloor(pipeline.data.min_resolution)
    setMaxSize(String(pipeline.data.max_size_gb))
  }, [pipeline.data])

  function done(ok: boolean) {
    setFlash(ok ? 'saved' : 'error')
    window.setTimeout(() => setFlash(null), 1400)
  }
  async function saveFloor(value: string) {
    const s = pipeline.data
    if (!s) return
    setFloor(value)
    try {
      await setPipelineSettings({ ...s, min_resolution: value })
      queryClient.invalidateQueries({ queryKey: ['settings', 'pipeline'] })
      done(true)
    } catch {
      done(false)
    }
  }
  async function saveMaxSize() {
    const s = pipeline.data
    if (!s) return
    const n = Number(maxSize)
    if (!(n > s.min_size_gb) || n === s.max_size_gb) {
      setMaxSize(String(s.max_size_gb))
      return
    }
    try {
      await setPipelineSettings({ ...s, max_size_gb: n })
      queryClient.invalidateQueries({ queryKey: ['settings', 'pipeline'] })
      done(true)
    } catch {
      done(false)
    }
  }

  if (pipeline.isLoading) {
    return (
      <div className="dash-card" aria-hidden="true">
        <div className="dash-card-head">
          <h3>Quick settings</h3>
        </div>
        <div className="dash-quick">
          <label>
            <span>Lowest quality to accept</span>
            <Skel className="dash-quick-skel">2160p / 4K</Skel>
          </label>
          <label>
            <span>Largest download</span>
            <Skel className="dash-quick-skel">100 GB</Skel>
          </label>
        </div>
        <p className="dash-card-foot">The same two settings as under Downloads, close to hand.</p>
      </div>
    )
  }
  if (floor == null || maxSize == null) return null
  return (
    <div className="dash-card">
      <div className="dash-card-head">
        <h3>Quick settings</h3>
        <span className={`dash-flash${flash ? ` ${flash}` : ''}`}>{flash === 'saved' ? 'Saved' : flash === 'error' ? "Couldn't save" : ''}</span>
      </div>
      <div className="dash-quick">
        <label>
          <span>Lowest quality to accept</span>
          <select value={floor} onChange={(e) => saveFloor(e.target.value)}>
            {RESOLUTION_OPTIONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Largest download</span>
          <span className="dash-quick-unit">
            <input type="number" min="1" step="1" value={maxSize} onChange={(e) => setMaxSize(e.target.value)} onBlur={saveMaxSize} />
            GB
          </span>
        </label>
      </div>
      <p className="dash-card-foot">The same two settings as under Downloads, close to hand.</p>
    </div>
  )
}

export default function DashboardPanel({ onOpen }: { onOpen: (key: string) => void }) {
  const storage = useQuery({ queryKey: ['storage-details'], queryFn: getStorageDetails, refetchInterval: 30_000 })
  const requests = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), refetchInterval: 5000 })
  const household = useQuery({ queryKey: ['household'], queryFn: listHousehold, staleTime: 60_000 })
  const recent = useQuery({ queryKey: ['plex-recently-added'], queryFn: getRecentlyAdded, staleTime: 120_000 })

  const d = storage.data
  const active = (requests.data ?? []).filter((r) => NON_TERMINAL.has(r.status))
  const downloading = active.filter((r) => r.status === 'downloading')
  const waiting = active.length - downloading.length
  const avatars = new Set((household.data ?? []).filter((u) => u.avatar).map((u) => u.plex_user_id))
  const added = recent.data?.available ? recent.data.items.slice(0, 6) : []
  const total = d?.available ? d.total_bytes : null
  const segments = d
    ? [
        ...d.libraries.map((lib) => ({ key: lib.key, label: lib.label, bytes: lib.bytes ?? 0, cls: lib.key === 'movies' ? 'movies' : 'tv' })),
        { key: 'other', label: 'Other', bytes: d.available ? Math.max(0, d.used_bytes - d.libraries.reduce((s, l) => s + (l.bytes ?? 0), 0)) : 0, cls: 'other' },
      ]
    : []

  return (
    <div className="dash">
      <div className="dash-stats">
        <Stat icon="download" tone="ice" value={downloading.length} label="Downloading now" loading={requests.isLoading} />
        <Stat icon="clock" tone="dim" value={waiting} label="Waiting" loading={requests.isLoading} />
        <Stat icon="check-circle" tone="mint" value={d?.completed_week ?? '—'} label="Added this week" loading={storage.isLoading} />
        <Stat icon="drive" tone="amber" value={d?.available ? formatBytes(d.free_bytes) : '—'} label="Free on the drive" loading={storage.isLoading} />
      </div>

      <div className="dash-grid">
        <div className="dash-card dash-storage">
          <div className="dash-card-head">
            <h3>Storage</h3>
            <button className="dash-link" onClick={() => onOpen('storage')}>
              Manage
              <Icon name="next" />
            </button>
          </div>
          {storage.isLoading ? (
            <StorageSkeleton />
          ) : d?.available ? (
            <>
              <div className="dash-storage-line">
                <b>{formatBytes(d.used_bytes)}</b>
                <span>of {formatBytes(d.total_bytes)} used | {Math.round(d.used_percent)}%</span>
              </div>
              <div className="dash-storage-bar" aria-hidden="true">
                {segments.map((s) => (
                  <i key={s.key} className={s.cls} style={{ width: total ? `${Math.min(100, (s.bytes / total) * 100)}%` : '0%' }} />
                ))}
              </div>
              <ul className="dash-storage-legend">
                {segments.map((s) => (
                  <li key={s.key}>
                    <i className={s.cls} />
                    {s.label}
                    <small>{formatBytes(s.bytes)}</small>
                  </li>
                ))}
                <li>
                  <i className="free" />
                  Free
                  <small>{formatBytes(d.free_bytes)}</small>
                </li>
              </ul>
            </>
          ) : (
            <p className="dash-empty">Obsidian can't see the library drive from here.</p>
          )}
        </div>

        <QuickSettings />

        <div className="dash-card dash-wide">
          <div className="dash-card-head">
            <h3>Downloading now</h3>
            <Link className="dash-link" to="/requests">
              All requests
              <Icon name="next" />
            </Link>
          </div>
          {requests.isLoading ? (
            <div className="dash-active-list">
              {Array.from({ length: 3 }, (_, i) => (
                <ActiveRowSkeleton key={i} />
              ))}
            </div>
          ) : active.length === 0 ? (
            <p className="dash-empty">Nothing on the way right now.</p>
          ) : (
            <div className="dash-active-list">
              {active.slice(0, 5).map((r) => (
                <ActiveRow key={r.id} r={r} hasAvatar={!!r.requested_by_plex_id && avatars.has(r.requested_by_plex_id)} />
              ))}
              {active.length > 5 && <p className="dash-card-foot">And {active.length - 5} more in Requests.</p>}
            </div>
          )}
        </div>

        <div className="dash-card dash-wide">
          <div className="dash-card-head">
            <h3>Recently added to Plex</h3>
          </div>
          {recent.isLoading ? (
            <div className="dash-recent" aria-hidden="true">
              {Array.from({ length: 6 }, (_, i) => (
                <div className="dash-recent-item" key={i}>
                  <PosterCardSkeleton meta={false} />
                  <small>
                    <SkelText width="50%" />
                  </small>
                </div>
              ))}
            </div>
          ) : added.length === 0 ? (
            <p className="dash-empty">{recent.data?.available === false ? 'Plex is not linked yet.' : 'Nothing added lately.'}</p>
          ) : (
            <div className="dash-recent">
              {added.map((it) => (
                <div className="dash-recent-item" key={it.rating_key}>
                  <PosterCard
                    item={{ id: it.tmdb_id ?? Number(it.rating_key), title: it.title, release_date: it.year ? `${it.year}-01-01` : null }}
                    mediaType={it.media_type}
                    mixed
                    posterSrc={it.poster_url}
                    href={it.tmdb_id ? (it.media_type === 'tv' ? `#/tv/${it.tmdb_id}` : `#/movies/${it.tmdb_id}`) : plexWebUrl(null, it.rating_key)}
                  />
                  <small>{it.added_at ? relativeTime(new Date(it.added_at * 1000).toISOString()) : ''}</small>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
