import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getTvSettings, setTvSettings } from '../../api/settings'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { ApiError } from '../../api/client'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

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

  if (query.isLoading) return <LoadingState />
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
      <p className="settings-sub">How Meridian watches for new episodes of the shows you follow.</p>
      <div className="settings-field">
        <label htmlFor="tvCheckInterval">Check for new episodes every (hours)</label>
        <input
          id="tvCheckInterval"
          type="number"
          min="0.5"
          step="0.5"
          value={checkInterval}
          onChange={(e) => setCheckInterval(e.target.value)}
        />
      </div>
      <div className="settings-field">
        <label htmlFor="tvAirBuffer">Wait after an episode airs (hours)</label>
        <input id="tvAirBuffer" type="number" min="0" step="1" value={airBuffer} onChange={(e) => setAirBuffer(e.target.value)} />
        <div className="settings-hint">Gives a copy time to appear. 0 means search straight away.</div>
      </div>
      <div className="settings-field">
        <label className="settings-checkbox-label">
          <input type="checkbox" checked={recheckEnabled} onChange={(e) => setRecheckEnabled(e.target.checked)} />
          Keep looking for missing or better copies
        </label>
        <div className="settings-hint">Tries again later when nothing was found, and upgrades when a better copy turns up.</div>
      </div>
      <div className="settings-field">
        <label htmlFor="tvRecheckInterval">Try again every (hours)</label>
        <input
          id="tvRecheckInterval"
          type="number"
          min="0.1"
          step="0.1"
          value={recheckInterval}
          onChange={(e) => setRecheckInterval(e.target.value)}
        />
      </div>
      <div className="settings-field">
        <label htmlFor="tvRecheckLimit">Give up after this many tries</label>
        <input
          id="tvRecheckLimit"
          type="number"
          min="0"
          step="1"
          value={recheckLimit}
          onChange={(e) => setRecheckLimit(e.target.value)}
        />
        <div className="settings-hint">0 means never give up.</div>
      </div>
      <button className="settings-btn" disabled={saveState === 'saving'} onClick={save}>
        {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save'}
      </button>
      {saveState === 'error' && saveError && <div className="settings-save-error">{saveError}</div>}
    </div>
  )
}
