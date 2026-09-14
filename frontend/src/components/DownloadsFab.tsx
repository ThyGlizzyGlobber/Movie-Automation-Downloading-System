import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'
import { listRequests } from '../api/requests'
import { NON_TERMINAL } from '../lib/status'
import './DownloadsFab.css'

// Polls independently of any per-page query so the badge count stays
// live no matter which page is showing — same query key ['requests']
// the Requests page itself will use once built, so TanStack Query
// dedupes the two into one shared poll rather than double-fetching.
export default function DownloadsFab() {
  const navigate = useNavigate()
  const location = useLocation()
  const { data } = useQuery({
    queryKey: ['requests'],
    queryFn: () => listRequests(),
    refetchInterval: 5000,
  })

  if (location.pathname === '/requests') return null

  const active = data?.filter((r) => NON_TERMINAL.has(r.status)).length ?? 0

  return (
    <button className="downloads-fab" aria-label="Requests" onClick={() => navigate('/requests')}>
      <span className="material-symbols-rounded">download</span>
      {active > 0 && <span className="downloads-badge">{active > 99 ? '99+' : active}</span>}
    </button>
  )
}
