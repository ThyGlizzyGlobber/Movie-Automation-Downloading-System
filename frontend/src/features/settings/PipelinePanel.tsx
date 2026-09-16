import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPipelineSettings, setPipelineSettings } from '../../api/settings'
import LanguageMultiSelect from '../../components/LanguageMultiSelect'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import { ApiError } from '../../api/client'
import SettingRow from '../../components/SettingRow'

// Shared with the dashboard's quick settings so both read as one setting.
export const RESOLUTION_OPTIONS = [
  { value: '2160p', label: '2160p / 4K' },
  { value: '1080p', label: '1080p' },
  { value: '720p', label: '720p' },
  { value: '480p', label: '480p' },
]

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

export default function PipelinePanel() {
  const query = useQuery({ queryKey: ['settings', 'pipeline'], queryFn: getPipelineSettings })

  const [minResolution, setMinResolution] = useState('2160p')
  const [minSize, setMinSize] = useState('0')
  const [maxSize, setMaxSize] = useState('0')
  const [allowlist, setAllowlist] = useState<string[]>([])
  const [required, setRequired] = useState<string[]>([])
  const [blocklist, setBlocklist] = useState<string[]>([])
  // Not exposed in this UI, but the settings endpoint's "always send the
  // full desired state, null = reset to default" convention means
  // omitting it on save would silently reset any previously-customized
  // qBittorrent category — stashed on load, sent back unchanged.
  const [category, setCategory] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!query.data) return
    setMinResolution(query.data.min_resolution)
    setMinSize(String(query.data.min_size_gb))
    setMaxSize(String(query.data.max_size_gb))
    setAllowlist(query.data.language_allowlist)
    setRequired(query.data.language_required)
    setBlocklist(query.data.language_blocklist)
    setCategory(query.data.category)
  }, [query.data])

  if (query.isLoading) return <LoadingState />
  if (query.isError) {
    return <ErrorState message={query.error instanceof Error ? query.error.message : undefined} />
  }

  async function save() {
    setSaveError(null)
    const min = Number(minSize)
    const max = Number(maxSize)
    if (!(min < max)) {
      setSaveError('The smallest size has to be less than the largest.')
      setSaveState('error')
      return
    }
    setSaveState('saving')
    try {
      await setPipelineSettings({
        category,
        min_resolution: minResolution,
        min_size_gb: min,
        max_size_gb: max,
        language_allowlist: allowlist,
        language_blocklist: blocklist,
        language_required: required,
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
      <h2>Downloads</h2>
      <p className="settings-sub">What counts as a good enough copy.</p>
      <SettingRow label="Lowest quality to accept" hint="Better copies are always preferred. This is just the floor." htmlFor="pfMinResolution">
        <select id="pfMinResolution" value={minResolution} onChange={(e) => setMinResolution(e.target.value)}>
          {RESOLUTION_OPTIONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </SettingRow>
      <SettingRow label="File size" hint="The smallest and largest download you'll accept, in GB.">
        <input type="number" min="0" step="1" value={minSize} onChange={(e) => setMinSize(e.target.value)} aria-label="Smallest size in GB" style={{ width: 90 }} />
        <span className="setting-unit">to</span>
        <input type="number" min="0" step="1" value={maxSize} onChange={(e) => setMaxSize(e.target.value)} aria-label="Largest size in GB" style={{ width: 90 }} />
        <span className="setting-unit">GB</span>
      </SettingRow>
      <div style={{ height: 8 }} />
      <div className="settings-field">
        <label htmlFor="pfAllowlist">Only these languages</label>
        <LanguageMultiSelect id="pfAllowlist" selected={allowlist} onChange={setAllowlist} />
      </div>
      <div className="settings-field">
        <label htmlFor="pfRequired">Must include these languages</label>
        <LanguageMultiSelect id="pfRequired" selected={required} onChange={setRequired} />
        <div className="settings-hint">For copies with more than one audio track.</div>
      </div>
      <div className="settings-field">
        <label htmlFor="pfBlocklist">Never these languages</label>
        <LanguageMultiSelect id="pfBlocklist" selected={blocklist} onChange={setBlocklist} />
      </div>
      <button className="settings-btn" disabled={saveState === 'saving'} onClick={save}>
        {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save'}
      </button>
      {saveState === 'error' && saveError && <div className="settings-save-error">{saveError}</div>}
    </div>
  )
}
