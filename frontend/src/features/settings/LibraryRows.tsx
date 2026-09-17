import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getLibrarySettings, setLibrarySettings } from '../../api/settings'
import { errorText, useToast } from '../../lib/toast'
import SettingRow from '../../components/SettingRow'
import Toggle from '../../components/Toggle'
import { Skel, SkelText } from '../../components/Skeleton'

// The rows' real labels with placeholder values while the settings load.
function LibraryRowsSkeleton({ part }: { part: 'library' | 'storage' }) {
  const control = <Skel className="setting-control-skel" />
  if (part === 'storage') {
    return (
      <SettingRow label="Free-space floor" hint="Downloads that would leave less than this free are skipped. 0 turns it off.">
        {control}
      </SettingRow>
    )
  }
  return (
    <>
      {['Movies library', 'TV library'].map((label) => (
        <div className="setting-row" key={label}>
          <div className="setting-row-text">
            <b>{label}</b>
            <small>
              <SkelText width="60%" />
            </small>
          </div>
          <div className="setting-row-control">{control}</div>
        </div>
      ))}
      <SettingRow label="Refresh Plex after import" hint="Ask Plex to scan the folder as soon as a file lands.">
        {control}
      </SettingRow>
    </>
  )
}

// Shared by the Plex card (library folders, refresh after import) and
// the Storage card (free-space floor): both edit the same settings
// record, so each shows its part and saves the whole.
export default function LibraryRows({ part }: { part: 'library' | 'storage' }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['library-settings'], queryFn: getLibrarySettings })
  const { toast } = useToast()
  const [movies, setMovies] = useState('')
  const [tv, setTv] = useState('')
  const [refresh, setRefresh] = useState(true)
  const [floor, setFloor] = useState('0')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!query.data) return
    setMovies(query.data.movie_library_root)
    setTv(query.data.tv_library_root)
    setRefresh(query.data.plex_refresh_after_import)
    setFloor(String(query.data.free_space_floor_gb))
  }, [query.data])

  if (query.isLoading) return <LibraryRowsSkeleton part={part} />
  if (!query.data) return null
  const fromEnv = query.data.source === 'env'
  const dirty =
    movies !== query.data.movie_library_root ||
    tv !== query.data.tv_library_root ||
    refresh !== query.data.plex_refresh_after_import ||
    Number(floor) !== query.data.free_space_floor_gb

  async function save() {
    setSaving(true)
    try {
      await setLibrarySettings({
        movie_library_root: fromEnv ? null : movies,
        tv_library_root: fromEnv ? null : tv,
        plex_refresh_after_import: refresh,
        free_space_floor_gb: Math.max(0, Number(floor) || 0),
      })
      queryClient.invalidateQueries({ queryKey: ['library-settings'] })
      queryClient.invalidateQueries({ queryKey: ['storage-details'] })
      toast({ tone: 'ok', title: 'Saved' })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't save that", body: errorText(err) })
    } finally {
      setSaving(false)
    }
  }

  const saveRow = dirty ? (
    <div className="settings-btn-row" style={{ marginTop: 14 }}>
      <button className="settings-btn sm" disabled={saving} onClick={save}>
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  ) : null

  if (part === 'storage') {
    return (
      <>
        <SettingRow label="Free-space floor" hint="Downloads that would leave less than this free are skipped. 0 turns it off." htmlFor="libFloor">
          <input id="libFloor" type="number" min="0" step="10" value={floor} onChange={(e) => setFloor(e.target.value)} />
          <span className="setting-unit">GB</span>
        </SettingRow>
        {saveRow}
      </>
    )
  }

  return (
    <div style={{ marginTop: 14 }}>
      <SettingRow label="Movies library" hint={fromEnv ? 'Set on the server. Change it there.' : 'Where finished movies are filed.'} htmlFor="libMovies">
        <input id="libMovies" className="mono" value={movies} disabled={fromEnv} onChange={(e) => setMovies(e.target.value)} style={{ width: 240 }} />
      </SettingRow>
      <SettingRow label="TV library" hint={fromEnv ? 'Set on the server. Change it there.' : 'Where finished episodes are filed.'} htmlFor="libTv">
        <input id="libTv" className="mono" value={tv} disabled={fromEnv} onChange={(e) => setTv(e.target.value)} style={{ width: 240 }} />
      </SettingRow>
      <SettingRow label="Refresh Plex after import" hint="Ask Plex to scan the folder as soon as a file lands.">
        <Toggle checked={refresh} onChange={setRefresh} label="Refresh Plex after import" />
      </SettingRow>
      {saveRow}
    </div>
  )
}
