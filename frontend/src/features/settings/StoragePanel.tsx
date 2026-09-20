import { useQuery } from '@tanstack/react-query'
import { getStorageDetails } from '../../api/settings'
import ProgressRing from '../../components/ProgressRing'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import { formatBytes } from '../../lib/format'
import LibraryRows from './LibraryRows'

// The reference's Settings › Storage: the library disk as a ring, how
// much of it each library holds, and request throughput. Library sizes
// are walked on the server and cached for ten minutes.
const ACTIVITY_SUB = "What's moving right now, and what landed recently."

export default function StoragePanel() {
  const query = useQuery({ queryKey: ['storage-details'], queryFn: getStorageDetails, refetchInterval: 30_000 })
  if (query.isLoading) {
    return (
      <>
        <SettingsCardSkeleton title="Storage" subSample="1.2 TB of 3.6 TB used." rows={3} />
        <SettingsCardSkeleton title="Activity" sub={ACTIVITY_SUB} rows={2} />
      </>
    )
  }
  const d = query.data
  if (!d) return <div className="settings-panel-card">Couldn't read storage.</div>
  const total = d.available ? d.total_bytes : null
  const bars = [
    ...d.libraries.map((lib) => ({ label: lib.label, bytes: lib.bytes, tone: lib.key === 'movies' ? 'var(--obsidian-ice)' : 'var(--obsidian-violet)' })),
    { label: 'Free', bytes: d.available ? d.free_bytes : null, tone: 'rgba(255,255,255,.35)' },
  ]
  return (
    <>
      <div className="settings-panel-card">
        <h2>Storage</h2>
        <p className="settings-sub">
          {d.available
            ? `${formatBytes(d.used_bytes)} of ${formatBytes(d.total_bytes)} used.`
            : "Obsidian can't see the library drive from here."}
        </p>
        <div className="storage-layout">
          <div className="storage-ring">
            <ProgressRing progress={d.available ? d.used_percent / 100 : 0} size={120} />
          </div>
          <div className="storage-bars">
            {bars.map((b) => (
              <div className="storage-bar" key={b.label}>
                <span>{b.label}</span>
                <i>
                  <b style={{ width: total && b.bytes != null ? `${Math.min(100, (b.bytes / total) * 100)}%` : '0%', background: b.tone }} />
                </i>
                <small>{formatBytes(b.bytes)}</small>
              </div>
            ))}
          </div>
        </div>
        <LibraryRows part="storage" />
      </div>
      <div className="settings-panel-card">
        <h2>Activity</h2>
        <p className="settings-sub">{ACTIVITY_SUB}</p>
        <div className="storage-tiles">
          <div className="storage-tile">
            <small>Downloading</small>
            <b>{d.downloading}</b>
          </div>
          <div className="storage-tile">
            <small>Queued</small>
            <b>{d.queued}</b>
          </div>
          <div className="storage-tile">
            <small>Added today</small>
            <b>{d.completed_today}</b>
          </div>
          <div className="storage-tile">
            <small>Added this week</small>
            <b>{d.completed_week}</b>
          </div>
        </div>
      </div>
    </>
  )
}
