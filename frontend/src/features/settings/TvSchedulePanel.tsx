import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getTvSettings, setTvSettings } from '../../api/settings'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import ErrorState from '../../components/ErrorState'
import { ApiError } from '../../api/client'
import SettingRow from '../../components/SettingRow'
import Toggle from '../../components/Toggle'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

const TV_SUB = 'How Obsidian watches for new episodes of the shows you follow.'

export default function TvSchedulePanel() {
  const query = useQuery({ queryKey: ['settings', 'tv'], queryFn: getTvSettings })

  const [checkInterval, setCheckInterval] = useState('6')
  const [airBuffer, setAirBuffer] = useState('0')
  const [recheckEnabled, setRecheckEnabled] = useState(false)
  const [recheckInterval, setRecheckInterval] = useState('1')
  const [recheckLimit, setRecheckLimit] = useState('0')
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!query.data) return
    setCheckInterval(String(query.data.show_check_interval_hours))
    setAirBuffer(String(query.data.episode_air_buffer_hours))
    setRecheckEnabled(query.data.episode_recheck_enabled)
    setRecheckInterval(String(query.data.episode_recheck_interval_hours))
    setRecheckLimit(String(query.data.episode_recheck_max_attempts))
  }, [query.data])

  if (query.isLoading) return <SettingsCardSkeleton title="TV shows" sub={TV_SUB} rows={5} />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }

  async function save() {
    setSaveError(null)
    setSaveState('saving')
    try {
      await setTvSettings({
        show_check_interval_hours: Number(checkInterval),
        episode_air_buffer_hours: Number(airBuffer),
        episode_recheck_enabled: recheckEnabled,
        episode_recheck_interval_hours: Number(recheckInterval),
        episode_recheck_max_attempts: Number(recheckLimit),
      })
      setSaveState('saved')
      setTimeout(() => setSaveState('idle'), 1200)
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : 'Something went wrong.')
      setSaveState('error')
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>TV shows</h2>
      <p className="settings-sub">{TV_SUB}</p>
      <SettingRow label="Check for new episodes every" hint="How often Obsidian looks at the shows you follow." htmlFor="tvCheckInterval">
        <input
          id="tvCheckInterval"
          type="number"
          min="0.5"
          step="0.5"
          value={checkInterval}
          onChange={(e) => setCheckInterval(e.target.value)}
        />
        <span className="setting-unit">hours</span>
      </SettingRow>
      <SettingRow
        label="Wait after an episode airs"
        hint="Gives a copy time to appear. 0 means search straight away."
        htmlFor="tvAirBuffer"
      >
        <input id="tvAirBuffer" type="number" min="0" step="1" value={airBuffer} onChange={(e) => setAirBuffer(e.target.value)} />
        <span className="setting-unit">hours</span>
      </SettingRow>
      <SettingRow
        label="Keep looking for missing or better copies"
        hint="Tries again later when nothing was found, and upgrades when a better copy turns up."
      >
        <Toggle checked={recheckEnabled} onChange={setRecheckEnabled} label="Keep looking for missing or better copies" />
      </SettingRow>
      <SettingRow label="Try again every" htmlFor="tvRecheckInterval">
        <input
          id="tvRecheckInterval"
          type="number"
          min="0.1"
          step="0.1"
          value={recheckInterval}
          onChange={(e) => setRecheckInterval(e.target.value)}
        />
        <span className="setting-unit">hours</span>
      </SettingRow>
      <SettingRow label="Give up after" hint="0 means never give up." htmlFor="tvRecheckLimit">
        <input id="tvRecheckLimit" type="number" min="0" step="1" value={recheckLimit} onChange={(e) => setRecheckLimit(e.target.value)} />
        <span className="setting-unit">tries</span>
      </SettingRow>
      <div className="settings-btn-row" style={{ marginTop: 18 }}>
        <button className="settings-btn" disabled={saveState === 'saving'} onClick={save}>
          {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save'}
        </button>
      </div>
      {saveState === 'error' && saveError && <div className="settings-save-error">{saveError}</div>}
    </div>
  )
}
