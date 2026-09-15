import { useQuery } from '@tanstack/react-query'
import { getStorageDetails } from '../../api/settings'
import ProgressRing from '../../components/ProgressRing'
import LoadingState from '../../components/LoadingState'
import { formatBytes } from '../../lib/format'

// The reference's Settings › Storage: the library disk as a ring, how
// much of it each library holds, and request throughput. Library sizes
// are walked on the server and cached for ten minutes.
export default function StoragePanel() {
  const query = useQuery({ queryKey: ['storage-details'], queryFn: getStorageDetails, refetchInterval: 30_000 })
  if (query.isLoading) return <LoadingState />
  const d = query.data
  if (!d) return <div className="settings-panel-card">Couldn't read storage.</div>
  const total = d.available ? d.total_bytes : null
  const bars = [
    ...d.libraries.map((lib) => ({ label: lib.label, bytes: lib.bytes, tone: lib.key === 'movies' ? 'var(--meridian-ice)' : 'var(--meridian-violet)' })),
    { label: 'Free', bytes: d.available ? d.free_bytes : null, tone: 'rgba(255,255,255,.35)' },
  ]
  return (
    <>
      <div className="settings-panel-card">
        <h2>Storage</h2>
        <p className="settings-hint">
          {d.available
            ? `${formatBytes(d.used_bytes)} of ${formatBytes(d.total_bytes)} used on the library disk.`
            : 'The library disk is not mounted here, so usage is unknown.'}
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
        <p className="settings-hint">
          Library folders: {d.libraries.map((l) => `${l.label} · ${l.root}`).join(' — ')}
        </p>
      </div>
      <div className="settings-panel-card">
        <h2>Throughput</h2>
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
