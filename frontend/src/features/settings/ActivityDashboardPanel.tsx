import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getActivity } from '../../api/settings'
import StatusPill from '../../components/StatusPill'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { formatBytes } from '../../lib/format'
import type { RequestPick } from '../../types/requests'
import './ActivityDashboardPanel.css'

const PAGE_SIZE = 25

const PEOPLE_SUB = 'Who has asked for what.'
const REQUESTS_SUB = 'What the pipeline picked, and how healthy the swarm behind it was.'

/** The swarm size behind a pick, as a label.
 *
 *  "Not reported" is not a cosmetic case — a plugin that returns no
 *  seeder count is exactly how a dead torrent gets chosen, since there
 *  is nothing to weigh it down at ranking time. Worth saying out loud
 *  rather than rendering a bare dash that reads like missing UI. */
function seedsLabel(nbSeeders: number | null | undefined): { text: string; unknown: boolean } {
  if (nbSeeders == null || nbSeeders < 0) return { text: 'seeds not reported', unknown: true }
  return { text: `${nbSeeders.toLocaleString()} ${nbSeeders === 1 ? 'seed' : 'seeds'}`, unknown: false }
}

export default function ActivityDashboardPanel() {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const query = useQuery({ queryKey: ['activity', limit], queryFn: () => getActivity(limit, 0) })

  if (query.isLoading) {
    return (
      <>
        <SettingsCardSkeleton title="Requests by person" sub={PEOPLE_SUB} rows={3} style={{ marginBottom: 16 }} />
        <SettingsCardSkeleton title="Recent requests" sub={null} rows={5} />
      </>
    )
  }
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }
  const data = query.data!

  return (
    <>
      <div className="settings-panel-card" style={{ marginBottom: 16 }}>
        <h2>Requests by person</h2>
        <p className="settings-sub">{PEOPLE_SUB}</p>
        {data.user_stats.length === 0 ? (
          <EmptyState message="No requests yet." />
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
        <h2>Recent requests</h2>
        <p className="settings-sub">{REQUESTS_SUB}</p>
        {data.requests.length === 0 ? (
          <EmptyState message="Nothing requested yet." />
        ) : (
          <div className="activity-request-list">
            {data.requests.map((r) => {
              const pick = (r.result ?? null) as RequestPick | null
              const winner = pick?.winner
              const seeds = seedsLabel(winner?.nbSeeders)
              return (
                <div className="activity-request-row" key={r.id}>
                  <div className="activity-request-main">
                    <span className="activity-request-title">{r.title}</span>
                    <span className="activity-request-sub">
                      {r.requested_by_username || 'System'} ·{' '}
                      {new Date(r.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}
                    </span>
                    {winner?.fileName && (
                      <>
                        <span className="activity-pick-name" title={winner.fileName}>
                          {winner.fileName}
                        </span>
                        <span className="activity-pick-meta">
                          <span className={seeds.unknown ? 'activity-pick-warn' : 'activity-pick-seeds'}>{seeds.text}</span>
                          {winner.engineName ? <> · {winner.engineName}</> : null}
                          {winner.fileSize ? <> · {formatBytes(winner.fileSize)}</> : null}
                          {pick?.candidates_considered ? <> · chosen from {pick.candidates_considered}</> : null}
                          {pick?.stall_attempts && pick.stall_attempts > 1 ? (
                            <> · <span className="activity-pick-warn">attempt {pick.stall_attempts}</span></>
                          ) : null}
                        </span>
                      </>
                    )}
                  </div>
                  <StatusPill status={r.status} />
                </div>
              )
            })}
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
