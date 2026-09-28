import { useQuery } from '@tanstack/react-query'
import { getAbout } from '../../api/admin'
import { formatBytes } from '../../lib/format'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import SettingRow from './SettingRow'

function uptime(seconds: number | null): string {
  if (seconds == null) return 'unknown'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

// "Build 1a2b3c4 | 28 Sep 2026", or what's missing when git couldn't say.
function buildLine(commit: string | null, date: string | null): string {
  if (!commit) return 'Unknown build'
  const day = date ? new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : null
  return day ? `Build ${commit} | ${day}` : `Build ${commit}`
}

const ABOUT_SUB ='Version, uptime, and what this install is connected to.'

export default function AboutPanel() {
  const query = useQuery({ queryKey: ['about'], queryFn: getAbout, refetchInterval: 60_000 })
  if (query.isLoading || !query.data) return <SettingsCardSkeleton title="About" sub={ABOUT_SUB} rows={6} />
  const a = query.data
  return (
    <div className="settings-panel-card">
      <h2>About</h2>
      <p className="settings-sub">{ABOUT_SUB}</p>
      <SettingRow label="Version" hint={buildLine(a.commit, a.date)}>
        <span className="about-value mono">{a.version ? `v${a.version}` : '—'}</span>
      </SettingRow>
      <SettingRow label="Running for" hint={a.started_at ? `Since ${new Date(a.started_at).toLocaleString()}` : undefined}>
        <span className="about-value">{uptime(a.uptime_seconds)}</span>
      </SettingRow>
      <SettingRow label="Plex server">
        <span className="about-value">{a.plex_server_name ?? 'Not connected'}</span>
      </SettingRow>
      <SettingRow label="Library folders" hint={`${a.movie_library_root} | ${a.tv_library_root}`}>
        <span className="about-value">Movies | TV</span>
      </SettingRow>
      <SettingRow label="History" hint={`${a.users} ${a.users === 1 ? 'person' : 'people'} | database ${a.db_bytes != null ? formatBytes(a.db_bytes) : '—'}`}>
        <span className="about-value">{a.requests.toLocaleString()} requests</span>
      </SettingRow>
      <SettingRow label="Runtime" hint={`Python ${a.python}`}>
        <span className="about-value">FastAPI | React</span>
      </SettingRow>
    </div>
  )
}
