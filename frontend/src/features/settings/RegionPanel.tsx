import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getRegionSettings, setRegionSettings } from '../../api/settings'
import { CERTIFICATION_REGIONS } from '../../lib/regions'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import ErrorState from '../../components/ErrorState'
import SettingRow from '../../components/SettingRow'

const REGION_SUB =
  "Ratings, streaming services and release dates all differ by country: the same show is TV-MA in the United States and MA15+ in Australia, and Stan exists in one of them and not the other. Pick where you are and Obsidian answers in that country everywhere."

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
      // page reads its ratings from — invalidate it and they change
      // without a reload.
      queryClient.invalidateQueries({ queryKey: ['settings', 'region'] })
      queryClient.invalidateQueries({ queryKey: ['session'] })
      // And everything else, unfiltered. The region is no longer only a
      // ratings switch: the backend now answers provider rows, coming
      // soon and digital release dates in it too (api.py's
      // resolve_region), and none of those carry it in their query key,
      // because it isn't a parameter the frontend passes any more.
      // Naming the affected keys here would mean keeping a list in sync
      // with every future row — a blanket refetch on a settings change
      // nobody makes twice a day is the cheaper promise to keep.
      queryClient.invalidateQueries()
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
      <SettingRow label="Country" hint="Used for ratings, availability and release dates." htmlFor="certification-region">
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
