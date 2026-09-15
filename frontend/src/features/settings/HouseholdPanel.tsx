import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listHousehold, removeHouseholdUser, setHouseholdUser } from '../../api/admin'
import { initialsOf } from '../../lib/useActiveRequestCount'
import { relativeTime } from '../../lib/format'
import { errorText, useToast } from '../../lib/toast'
import Toggle from '../../components/Toggle'
import LoadingState from '../../components/LoadingState'
import Icon from '../../components/Icon'

// Settings › Household: everyone who has signed in, whether they can
// request, and a way to remove someone who no longer should be here.
export default function HouseholdPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['household'], queryFn: listHousehold })
  const { toast } = useToast()
  const [busy, setBusy] = useState<string | null>(null)

  if (query.isLoading || !query.data) return <LoadingState />
  const users = query.data

  async function setCanRequest(id: string, on: boolean) {
    setBusy(id)
    try {
      await setHouseholdUser(id, on)
      queryClient.invalidateQueries({ queryKey: ['household'] })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't change that", body: errorText(err) })
    } finally {
      setBusy(null)
    }
  }

  async function remove(id: string, name: string) {
    if (!confirm(`Remove ${name}? They are signed out everywhere and can sign in again only if they still have access to the Plex server.`)) return
    setBusy(id)
    try {
      await removeHouseholdUser(id)
      queryClient.invalidateQueries({ queryKey: ['household'] })
      queryClient.invalidateQueries({ queryKey: ['audit-log'] })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't remove them", body: errorText(err) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Household</h2>
      <p className="settings-sub">Everyone who has signed in with Plex, and who can ask for things.</p>
      <div className="household-list">
        {users.map((u) => {
          const name = u.username || u.plex_user_id
          return (
            <div className="setting-row household-row" key={u.plex_user_id}>
              <div className="household-who">
                <span className="household-avatar">{initialsOf(name) || '?'}</span>
                <div className="setting-row-text">
                  <b>
                    {name}
                    {u.is_admin && <span className="household-tag">Admin</span>}
                  </b>
                  <small>
                    {u.requests} request{u.requests === 1 ? '' : 's'} · signed in {relativeTime(u.last_login_at)}
                  </small>
                </div>
              </div>
              <div className="setting-row-control">
                <span className="setting-unit">Can request</span>
                <Toggle
                  checked={u.is_admin || u.can_request}
                  onChange={(v) => {
                    if (!u.is_admin && busy !== u.plex_user_id) setCanRequest(u.plex_user_id, v)
                  }}
                  label={`${name} can request`}
                />
                {!u.is_admin && (
                  <button className="rq-circ danger" aria-label={`Remove ${name}`} disabled={busy === u.plex_user_id} onClick={() => remove(u.plex_user_id, name)}>
                    <Icon name="trash" />
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
