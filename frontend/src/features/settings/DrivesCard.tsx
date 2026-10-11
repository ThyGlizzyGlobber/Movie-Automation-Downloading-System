import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { addDrive, getDrives } from '../../api/drives'
import { ApiError } from '../../api/client'
import { formatBytes } from '../../lib/format'
import type { BoxDrive, FreeDisk } from '../../types/features'

const SUB = 'Each drive is its own storage. New downloads go to the one with the most room.'
const STATE_LABEL: Record<BoxDrive['state'], string> = {
  healthy: 'Healthy',
  problem: 'Needs attention',
  missing: 'Not found',
}

// Settings › Storage, on a box: its drives, and adding one. Hidden elsewhere
// (StoragePanel only shows it when the box manages its own drives).
export default function DrivesCard() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['drives'],
    queryFn: getDrives,
    staleTime: 0,
    // Quick while a drive is being added; otherwise often enough that a
    // drive slid into a bay shows up within a minute or so.
    refetchInterval: (q) => {
      const data = q.state.data
      return data?.managed && (data.pending || data.last?.state === 'adding') ? 4000 : 20_000
    },
    refetchIntervalInBackground: true,
  })
  const data = query.data
  if (!data?.managed) return null

  const adding = data.pending || data.last?.state === 'adding'
  const done = () => queryClient.invalidateQueries({ queryKey: ['drives'] })

  return (
    <div className="settings-panel-card">
      <h2>Drives</h2>
      <p className="settings-sub">{SUB}</p>

      {data.drives.length === 0 && !adding && (
        <p className="drive-callout">
          This box has no hard drive yet. Slide a 3.5" hard drive of any size into a free bay, and it shows up below
          within a minute. Downloads wait until there's a drive.
        </p>
      )}

      {data.drives.length > 0 && (
        <div className="drive-list">
          {data.drives.map((drive) => (
            <DriveRow key={drive.id} drive={drive} />
          ))}
        </div>
      )}

      {adding && (
        <p className="plex-hint" role="status">
          Adding the drive. This takes about a minute, and Plex and downloads pause for a few seconds while it's mounted.
        </p>
      )}
      {!adding && data.last?.state === 'failed' && <p className="plex-error">{data.last.message}</p>}
      {!adding && data.last?.state === 'added' && isRecent(data.last.at) && <p className="plex-hint">{data.last.message}</p>}

      {!adding && data.unused.length > 0 && (
        <div className="drive-new">
          <h3>New drives</h3>
          {data.unused.map((disk) => (
            <NewDisk key={disk.serial} disk={disk} canAdd={data.updater_running} onAdded={done} />
          ))}
        </div>
      )}
      {!data.report_fresh && (
        <p className="plex-hint">
          Can't check for new drives right now: the box hasn't reported its drives in the last few minutes.
        </p>
      )}
    </div>
  )
}

function DriveRow({ drive }: { drive: BoxDrive }) {
  const used = drive.used != null && drive.size ? Math.min(100, (drive.used / drive.size) * 100) : 0
  return (
    <div className="drive-row">
      <div className="drive-row-head">
        <b>{drive.label}</b>
        <span className="drive-size">{formatBytes(drive.size)}</span>
        <span className={`drive-state ${drive.state}`}>{STATE_LABEL[drive.state]}</span>
      </div>
      {drive.state === 'missing' ? (
        <p className="drive-note">
          {drive.label} can't be reached. If it was taken out, put it back in. If it has failed, what was on it will need
          downloading again.
        </p>
      ) : (
        <>
          <div className="storage-bar drive-bar">
            <i>
              <b style={{ width: `${used}%`, background: drive.state === 'problem' ? 'var(--status-failed)' : 'var(--obsidian-ice)' }} />
            </i>
          </div>
          <small className="drive-note">
            {formatBytes(drive.used)} used · {formatBytes(drive.free)} free
            {drive.state === 'problem' && ` · TrueNAS reports it as ${drive.status?.toLowerCase() ?? 'unhealthy'}`}
          </small>
        </>
      )}
    </div>
  )
}

function NewDisk({ disk, canAdd, onAdded }: { disk: FreeDisk; canAdd: boolean; onAdded: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const [eraseOther, setEraseOther] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const kind = disk.type === 'SSD' ? 'SSD' : 'hard drive'
  const ready = typed.trim().toUpperCase() === 'ERASE' && (!disk.has_other_data || eraseOther)

  async function add() {
    setSaving(true)
    setError(null)
    try {
      await addDrive({ disk: disk.name, serial: disk.serial, confirm: true, erase_other_data: eraseOther })
      onAdded()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="drive-row">
      <div className="drive-row-head">
        <b>{formatBytes(disk.size)} {kind}</b>
        <span className="drive-size">{disk.model ?? disk.name}</span>
        {!confirming && (
          <button className="settings-btn sm" disabled={!canAdd} onClick={() => setConfirming(true)}>
            Add
          </button>
        )}
      </div>
      {!canAdd && <p className="drive-note">The box's updater isn't running, so drives can't be added right now.</p>}
      {confirming && (
        <div className="drive-confirm">
          <p className="plex-error">Adding it erases everything on this drive.</p>
          {disk.has_other_data && (
            <label className="drive-check" htmlFor={`erase-${disk.serial}`}>
              <input
                id={`erase-${disk.serial}`}
                type="checkbox"
                checked={eraseOther}
                onChange={(e) => setEraseOther(e.target.checked)}
              />
              This drive has data from another computer or NAS on it. Erase it.
            </label>
          )}
          <div className="settings-field">
            <label htmlFor={`confirm-${disk.serial}`}>Type ERASE to confirm</label>
            <input
              id={`confirm-${disk.serial}`}
              type="text"
              autoComplete="off"
              autoCapitalize="characters"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </div>
          {error && <p className="plex-error">{error}</p>}
          <div className="settings-btn-row">
            <button className="settings-btn" disabled={!ready || saving} onClick={add}>
              {saving ? 'Adding…' : 'Erase and add drive'}
            </button>
            <button className="settings-btn ghost" disabled={saving} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function isRecent(at: string): boolean {
  return Date.now() - new Date(at).getTime() < 10 * 60_000
}
