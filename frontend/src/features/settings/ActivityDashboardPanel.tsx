import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getActivity } from '../../api/settings'
import StatusPill from '../../components/StatusPill'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import './ActivityDashboardPanel.css'

const PAGE_SIZE = 25

export default function ActivityDashboardPanel() {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const query = useQuery({ queryKey: ['activity', limit], queryFn: () => getActivity(limit, 0) })

  if (query.isLoading) return <LoadingState />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }
  const data = query.data!

  return (
    <>
      <div className="settings-panel-card" style={{ marginBottom: 16 }}>
        <h2>Who's requesting</h2>
        {data.user_stats.length === 0 ? (
          <EmptyState message="No attributed requests yet." />
        ) : (
          <div className="activity-user-list">
            {data.user_stats.map((s) => (
              <div className="activity-user-row" key={s.plex_user_id}>
                <span className="activity-user-name">{s.username || 'Unknown'}</span>
                <span className="activity-user-counts">
                  {s.total_requests} total · {s.requests_this_month} this month
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="settings-panel-card">
        <h2>Recent activity</h2>
        {data.requests.length === 0 ? (
          <EmptyState message="Nothing requested yet." />
        ) : (
          <div className="activity-request-list">
            {data.requests.map((r) => (
              <div className="activity-request-row" key={r.id}>
                <div className="activity-request-main">
                  <span className="activity-request-title">{r.title}</span>
                  <span className="activity-request-sub">
                    {r.requested_by_username || 'System'} ·{' '}
                    {new Date(r.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}
                  </span>
                </div>
                <StatusPill status={r.status} />
              </div>
            ))}
          </div>
        )}
        {data.total > data.requests.length && (
          <button className="settings-btn secondary" style={{ marginTop: 16 }} onClick={() => setLimit((l) => l + PAGE_SIZE)}>
            Load more
          </button>
        )}
      </div>
    </>
  )
}
