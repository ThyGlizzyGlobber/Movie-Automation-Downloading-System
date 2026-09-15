import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getQualityProfilesAdmin, setQualityProfiles } from '../../api/settings'
import LoadingState from '../../components/LoadingState'
import Icon from '../../components/Icon'
import type { QualityProfile } from '../../types/features'

const RESOLUTIONS = [
  { value: '', label: 'Household default (Pipeline & Quality)' },
  { value: '2160p', label: '2160p / 4K minimum' },
  { value: '1080p', label: '1080p minimum' },
  { value: '720p', label: '720p minimum' },
  { value: '480p', label: '480p minimum' },
]

function slug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'profile'
}

// Settings › Quality profiles: the named choices the request sheet
// offers. Each is a friendly name over a resolution floor plus a typical
// size shown next to it; one is the default for one-tap requests.
export default function QualityProfilesPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['quality-profiles-admin'], queryFn: getQualityProfilesAdmin })
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [defaultId, setDefaultId] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (query.data) {
      setProfiles(query.data.profiles)
      setDefaultId(query.data.default_profile_id)
    }
  }, [query.data])

  if (query.isLoading) return <LoadingState />

  function update(i: number, patch: Partial<QualityProfile>) {
    setProfiles((list) => list.map((p, j) => (j === i ? { ...p, ...patch } : p)))
    setSaved(false)
  }
  function add() {
    const base = slug('New profile')
    let id = base
    let n = 2
    while (profiles.some((p) => p.id === id)) id = `${base}-${n++}`
    setProfiles((list) => [...list, { id, name: 'New profile', description: '', min_resolution: '1080p', typical_size_gb: null }])
    setSaved(false)
  }
  function remove(i: number) {
    const removed = profiles[i]
    const next = profiles.filter((_, j) => j !== i)
    setProfiles(next)
    if (removed.id === defaultId && next.length) setDefaultId(next[0].id)
    setSaved(false)
  }
  async function save() {
    setSaving(true)
    setError(null)
    try {
      const body = await setQualityProfiles({ profiles, default_profile_id: defaultId })
      setProfiles(body.profiles)
      setDefaultId(body.default_profile_id)
      setSaved(true)
      queryClient.invalidateQueries({ queryKey: ['quality-profiles'] })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Quality profiles</h2>
      <p className="settings-hint">
        What the household picks from when requesting. The resolution floor is the only knob the pipeline honours per
        request; the size is a typical figure shown next to the choice, not a limit.
      </p>
      <div className="profile-list">
        {profiles.map((p, i) => (
          <div className={`profile-card${p.id === defaultId ? ' default' : ''}`} key={p.id}>
            <div className="profile-card-head">
              <label className="profile-default">
                <input type="radio" name="default-profile" checked={p.id === defaultId} onChange={() => setDefaultId(p.id)} />
                Default
              </label>
              <button className="profile-remove" aria-label="Remove profile" disabled={profiles.length <= 1} onClick={() => remove(i)}>
                <Icon name="trash" />
              </button>
            </div>
            <div className="settings-field">
              <label>Name</label>
              <input value={p.name} onChange={(e) => update(i, { name: e.target.value })} />
            </div>
            <div className="settings-field">
              <label>Description</label>
              <input value={p.description ?? ''} onChange={(e) => update(i, { description: e.target.value })} placeholder="Shown under the name" />
            </div>
            <div className="settings-row">
              <div className="settings-field">
                <label>Resolution floor</label>
                <select value={p.min_resolution ?? ''} onChange={(e) => update(i, { min_resolution: e.target.value || null })}>
                  {RESOLUTIONS.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="settings-field">
                <label>Typical size (GB)</label>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={p.typical_size_gb ?? ''}
                  placeholder="varies"
                  onChange={(e) => update(i, { typical_size_gb: e.target.value === '' ? null : Number(e.target.value) })}
                />
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="settings-btn-row">
        <button className="settings-btn secondary" onClick={add}>
          <Icon name="plus" /> Add profile
        </button>
        <button className="settings-btn" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
      {error && <div className="settings-save-error">{error}</div>}
      {saved && <div className="settings-save-success">Saved.</div>}
    </div>
  )
}
