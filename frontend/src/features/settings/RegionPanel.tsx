import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getRegionSettings, setRegionSettings } from '../../api/settings'
import { CERTIFICATION_REGIONS } from '../../lib/regions'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import ErrorState from '../../components/ErrorState'
import SettingRow from '../../components/SettingRow'

const REGION_SUB =
  "Age ratings differ by country: the same show is TV-MA in the United States and MA15+ in Australia. Pick where you are and Obsidian shows that country's ratings everywhere."

export default function RegionPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['settings', 'region'], queryFn: getRegionSettings })
  const [region, setRegion] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (query.data) setRegion(query.data.certification_region)
  }, [query.data])

  if (query.isLoading) return <SettingsCardSkeleton title="Region" sub={REGION_SUB} rows={1} />
  if (query.isError) return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />

  async function save(next: string) {
    setRegion(next)
    setError(null)
    setSaving(true)
    try {
      await setRegionSettings({ certification_region: next })
      // The region rides down with the session, so that is what every
      // page reads it from — invalidate it and the ratings on screen
      // change without a reload.
      queryClient.invalidateQueries({ queryKey: ['settings', 'region'] })
      queryClient.invalidateQueries({ queryKey: ['session'] })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Region</h2>
      <p className="settings-sub">{REGION_SUB}</p>
      <SettingRow label="Country" hint="Used for age ratings." htmlFor="certification-region">
        <select
          id="certification-region"
          value={region}
          disabled={saving}
          onChange={(e) => void save(e.target.value)}
        >
          {CERTIFICATION_REGIONS.map((r) => (
            <option key={r.code} value={r.code}>
              {r.name}
            </option>
          ))}
        </select>
      </SettingRow>
      {error && <div className="settings-save-error">{error}</div>}
    </div>
  )
}
