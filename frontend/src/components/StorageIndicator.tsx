import { useQuery } from '@tanstack/react-query'
import { getStorage } from '../api/system'
import './StorageIndicator.css'
import Icon from './Icon'

// Disk usage moves far slower than the request queue, so this polls on
// its own, much longer interval than DownloadsFab's 5s. Hidden entirely
// (not just zeroed) whenever /api/storage reports unavailable — no real
// mount is the normal case for local dev, and a broken readout would be
// worse than no readout at all.
export default function StorageIndicator() {
  const { data } = useQuery({
    queryKey: ['storage'],
    queryFn: getStorage,
    refetchInterval: 60000,
  })

  if (!data?.available) return null

  const pct = Math.max(0, Math.min(100, data.used_percent))

  return (
    <div className="storage-indicator">
      <Icon name="drive" className="storage-indicator-icon" />
      <span className="storage-indicator-bar">
        <span className="storage-indicator-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="storage-indicator-pct">{Math.round(pct)}%</span>
    </div>
  )
}
