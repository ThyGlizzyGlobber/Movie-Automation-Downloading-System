import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getRetention, setRetention } from '../../api/settings'
import { RETENTION_OPTIONS } from '../../lib/retention'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { ApiError } from '../../api/client'

export default function RetentionPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['retention'], queryFn: getRetention })
  const [saving, setSaving] = useState<number | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  if (query.isLoading) return <LoadingState />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }

  async function choose(days: number | null) {
    setSaving(days)
    setError(null)
    try {
      await setRetention(days)
      queryClient.invalidateQueries({ queryKey: ['retention'] })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setSaving(undefined)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Requests history</h2>
      <div className="settings-hint" style={{ marginTop: 0, marginBottom: 16 }}>
        Automatically clears finished, cancelled, and failed requests older than this. Active downloads are never
        affected.
      </div>
      <div className="settings-server-list">
        {RETENTION_OPTIONS.map((o) => {
          const isSelected = (query.data?.days ?? null) === o.days
          return (
            <button
              key={String(o.days)}
              className={`settings-server-option${isSelected ? ' selected' : ''}`}
              disabled={saving !== undefined}
              onClick={() => choose(o.days)}
            >
              <span>{o.label}</span>
              {saving === o.days ? <span>Saving…</span> : isSelected ? <span>✓</span> : null}
            </button>
          )
        })}
      </div>
      {error && <div className="settings-save-error">{error}</div>}
    </div>
  )
}
