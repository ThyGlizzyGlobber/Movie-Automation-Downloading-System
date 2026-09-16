import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getPipelineSettings, getStorageDetails, setPipelineSettings } from '../../api/settings'
import { listHousehold } from '../../api/admin'
import { listRequests } from '../../api/requests'
import { getRecentlyAdded } from '../../api/plex'
import Avatar from '../../components/Avatar'
import Icon from '../../components/Icon'
import PosterCard from '../../components/PosterCard'
import StatusPill from '../../components/StatusPill'
import { formatBytes, plexWebUrl, relativeTime } from '../../lib/format'
import { posterUrl } from '../../lib/tmdbImage'
import { NON_TERMINAL, statusDetail } from '../../lib/status'
import { requestLabelAndHref } from '../../lib/requestGrouping'
import type { RequestOut } from '../../types/requests'
import './DashboardPanel.css'

// Settings opens here: the household at a glance. Four numbers, the
// library drive, who is downloading what right now, what Plex added
// lately, and the two download knobs people actually reach for.

const RESOLUTIONS = [
  { value: '480p', label: 'Anything' },
  { value: '720p', label: '720p or better' },
  { value: '1080p', label: '1080p or better' },
  { value: '2160p', label: '4K only' },
]

function Stat({ icon, tone, value, label }: { icon: Parameters<typeof Icon>[0]['name']; tone: string; value: string | number; label: string }) {
  return (
    <div className="dash-stat">
      <span className={`dash-stat-icon ${tone}`}>
        <Icon name={icon} />
      </span>
      <div>
        <b>{value}</b>
        <small>{label}</small>
      </div>
    </div>
  )
}

function ActiveRow({ r, hasAvatar }: { r: RequestOut; hasAvatar: boolean }) {
  const { href } = requestLabelAndHref(r)
  const pct = r.status === 'downloading' && r.download_progress != null ? Math.round(r.download_progress * 100) : null
  const who = r.requested_by_username || 'Meridian'
  return (
    <a className="dash-active" href={href}>
      <span className="dash-active-who" title={who}>
        {r.requested_by_plex_id ? (
          <Avatar src={`/api/admin/users/${encodeURIComponent(r.requested_by_plex_id)}/avatar`} name={who} hasPicture={hasAvatar} />
        ) : (
          <Icon name="gear" />
        )}
      </span>
      <img className="dash-active-poster" src={posterUrl(r.poster_path)} alt="" />
      <span className="dash-active-text">
        <b>{r.title}</b>
        <small>
          {who}
          {r.media_type === 'episode' && r.season_number != null ? ` · S${String(r.season_number).padStart(2, '0')}E${String(r.episode_number).padStart(2, '0')}` : ''}
          {r.media_type === 'pack' ? (r.season_number == null ? ' · Complete series' : ` · Season ${r.season_number}`) : ''}
          {' · '}
          {statusDetail(r.status, r.download_progress)}
        </small>
        <i className="dash-active-bar">
          <b style={{ width: `${pct ?? (r.status === 'downloading' ? 2 : 0)}%` }} />
        </i>
      </span>
      <StatusPill status={r.status} />
    </a>
  )
}

function QuickSettings() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['settings', 'pipeline'], queryFn: getPipelineSettings })
  const [floor, setFloor] = useState<string | null>(null)
  const [maxSize, setMaxSize] = useState<string | null>(null)
  const [flash, setFlash] = useState<'saved' | 'error' | null>(null)
  useEffect(() => {
    if (!query.data) return
    setFloor(query.data.min_resolution)
    setMaxSize(String(query.data.max_size_gb))
  }, [query.data])

  async function save(patch: { min_resolution?: string; max_size_gb?: number }) {
    const s = query.data
    if (!s) return
    try {
      await setPipelineSettings({
        category: s.category,
        min_resolution: patch.min_resolution ?? s.min_resolution,
        min_size_gb: s.min_size_gb,
        max_size_gb: patch.max_size_gb ?? s.max_size_gb,
        language_allowlist: s.language_allowlist,
        language_blocklist: s.language_blocklist,
        language_required: s.language_required,
      })
      queryClient.invalidateQueries({ queryKey: ['settings', 'pipeline'] })
      setFlash('saved')
    } catch {
      setFlash('error')
    }
    window.setTimeout(() => setFlash(null), 1400)
  }

  if (!query.data || floor == null || maxSize == null) return null
  return (
    <div className="dash-card">
      <div className="dash-card-head">
        <h3>Quick settings</h3>
        <span className={`dash-flash${flash ? ` ${flash}` : ''}`}>{flash === 'saved' ? 'Saved' : flash === 'error' ? "Couldn't save" : ''}</span>
      </div>
      <div className="dash-quick">
        <label>
          <span>Lowest quality to accept</span>
          <select
            value={floor}
            onChange={(e) => {
              setFloor(e.target.value)
              save({ min_resolution: e.target.value })
            }}
          >
            {RESOLUTIONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Largest download</span>
          <span className="dash-quick-unit">
            <input
              type="number"
              min="1"
              step="1"
              value={maxSize}
              onChange={(e) => setMaxSize(e.target.value)}
              onBlur={() => {
                const n = Number(maxSize)
                if (n > query.data!.min_size_gb && n !== query.data!.max_size_gb) save({ max_size_gb: n })
                else setMaxSize(String(query.data!.max_size_gb))
              }}
            />
            GB
          </span>
        </label>
      </div>
      <p className="dash-card-foot">Everything else lives under Downloads in the list on the left.</p>
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
        <Stat icon="download" tone="ice" value={downloading.length} label="Downloading now" />
        <Stat icon="clock" tone="dim" value={waiting} label="Waiting" />
        <Stat icon="check-circle" tone="mint" value={d?.completed_week ?? '—'} label="Added this week" />
        <Stat icon="drive" tone="amber" value={d?.available ? formatBytes(d.free_bytes) : '—'} label="Free on the drive" />
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
          {d?.available ? (
            <>
              <div className="dash-storage-line">
                <b>{formatBytes(d.used_bytes)}</b>
                <span>of {formatBytes(d.total_bytes)} used · {Math.round(d.used_percent)}%</span>
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
            <p className="dash-empty">Meridian can't see the library drive from here.</p>
          )}
        </div>

        <QuickSettings />

        <div className="dash-card dash-wide">
          <div className="dash-card-head">
            <h3>Downloading now</h3>
            <a className="dash-link" href="#/requests">
              All requests
              <Icon name="next" />
            </a>
          </div>
          {active.length === 0 ? (
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
          {added.length === 0 ? (
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
