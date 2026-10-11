import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getUpdates, installUpdate, postDeploy } from '../../api/admin'
import { ApiError } from '../../api/client'
import { useToast } from '../../lib/toast'
import type { UpdaterStatus, UpdatesInfo } from '../../types/features'
import { SettingsCardSkeleton } from './SettingsSkeleton'
import SettingRow from './SettingRow'

const SUB = 'Install the latest version of Obsidian.'
const BUSY: UpdaterStatus['state'][] = ['updating', 'verifying', 'rolling_back']

function isBusy(data: UpdatesInfo | undefined): boolean {
  return data?.method === 'image' && BUSY.includes(data.updater.state)
}

export default function UpdatesPanel() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [checking, setChecking] = useState(false)
  const [starting, setStarting] = useState(false)
  // While an update runs, the app restarts under this page: requests fail
  // for a while, so keep asking every few seconds rather than giving up —
  // in a background tab too, or someone who switches away while it installs
  // comes back to a page still saying "Installing" (the app's 5-minute
  // staleTime would otherwise skip the refetch on return).
  const query = useQuery({
    queryKey: ['updates'],
    queryFn: () => getUpdates(false),
    retry: false,
    staleTime: 0,
    refetchInterval: (q) => (starting || isBusy(q.state.data) ? 3000 : false),
    refetchIntervalInBackground: true,
  })
  const data = query.data

  // The version this page's own code came with. Once an update lands, that
  // code is stale, so the page reloads itself onto the new one.
  const loadedWith = useRef<string | null>(null)
  useEffect(() => {
    if (!data?.current) return
    if (loadedWith.current === null) loadedWith.current = data.current
    else if (data.method === 'image' && data.updater.state === 'done' && data.current !== loadedWith.current) {
      // Back onto this section, so the result is the first thing they see.
      window.location.hash = '/settings#settings-updates'
      window.location.reload()
    }
  }, [data])

  if (query.isLoading || !data) return <SettingsCardSkeleton title="Updates" sub={SUB} rows={2} />
  if (data.method === 'git') return <GitUpdates />

  const updater = data.updater
  const busy = isBusy(data)

  async function checkAgain() {
    setChecking(true)
    try {
      queryClient.setQueryData(['updates'], await getUpdates(true))
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't check for updates", body: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setChecking(false)
    }
  }

  async function install(version: string) {
    const ok = confirm(
      `Install Obsidian ${version}?\n\nObsidian restarts during the update, which takes a minute or two. ` +
        "A copy of the database is taken first, and if the new version doesn't start properly, Obsidian goes back to this one by itself.",
    )
    if (!ok) return
    setStarting(true)
    try {
      await installUpdate(version)
      await queryClient.invalidateQueries({ queryKey: ['updates'] })
    } catch (err) {
      toast({ tone: 'error', title: "Update didn't start", body: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Updates</h2>
      <p className="settings-sub">{SUB}</p>
      <SettingRow label="Version" hint={`Follows the ${data.channel} channel`}>
        <span className="about-value mono">{data.current ? `v${data.current}` : '—'}</span>
      </SettingRow>

      {busy ? (
        <Progress updater={updater} />
      ) : (
        <>
          {updater.finished_at && updater.message && <LastResult updater={updater} />}
          {data.error && <p className="plex-error">Couldn't check for updates: {data.error}</p>}
          {data.available && data.latest ? (
            <div className="update-available">
              <p className="update-headline">Version {data.latest} is available.</p>
              {data.notes.length > 0 && <ReleaseNotes notes={data.notes} />}
              <InstallControl
                updater={updater}
                version={data.latest}
                starting={starting}
                onInstall={install}
              />
            </div>
          ) : data.skipped ? (
            <p className="plex-hint">Version {data.skipped} won't be offered again. Obsidian will offer the next version once it's out.</p>
          ) : (
            !data.error && <p className="plex-hint">Obsidian is up to date.</p>
          )}
          <div className="settings-btn-row">
            <button className="settings-btn secondary sm" disabled={checking || starting} onClick={checkAgain}>
              {checking ? 'Checking…' : 'Check for updates'}
            </button>
          </div>
        </>
      )}
      <div className="about-footer">Obsidian</div>
    </div>
  )
}

function InstallControl({ updater, version, starting, onInstall }: {
  updater: UpdaterStatus
  version: string
  starting: boolean
  onInstall: (version: string) => void
}) {
  if (!updater.reachable) {
    return <p className="plex-hint">The updater isn't running on this box, so the update can't be installed from here right now.</p>
  }
  if (updater.configured === false) {
    return <p className="plex-hint">Automatic updates aren't set up on this box yet.</p>
  }
  return (
    <div className="settings-btn-row">
      <button className="settings-btn" disabled={starting} onClick={() => onInstall(version)}>
        {starting ? 'Starting…' : `Install v${version}`}
      </button>
    </div>
  )
}

function Progress({ updater }: { updater: UpdaterStatus }) {
  const line =
    updater.state === 'rolling_back'
      ? `${updater.message ?? "The update didn't start properly"}. Going back to v${updater.from_version}…`
      : updater.state === 'verifying'
        ? `Checking that v${updater.to_version} started properly…`
        : `Installing v${updater.to_version}…`
  return (
    <div className="update-progress" role="status">
      <span className="update-spinner" aria-hidden="true" />
      <div>
        <p className="update-headline">{line}</p>
        <p className="plex-hint">Obsidian restarts as part of this, so the page may stop answering for a minute. It comes back by itself.</p>
      </div>
    </div>
  )
}

function LastResult({ updater }: { updater: UpdaterStatus }) {
  const when = updater.finished_at ? new Date(updater.finished_at).toLocaleString() : ''
  const failed = updater.state === 'failed' || updater.state === 'rolled_back'
  return (
    <p className={failed ? 'plex-error' : 'plex-hint'}>
      {updater.message} {when && <span className="update-when">{when}</span>}
    </p>
  )
}

function ReleaseNotes({ notes }: { notes: { heading: string; items: string[] }[] }) {
  return (
    <div className="update-notes">
      {notes.map((group) => (
        <div key={group.heading}>
          <h3>{group.heading}</h3>
          <ul>
            {group.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

// A git-pull install (the development box): pull the clone and rebuild.
function GitUpdates() {
  const [deploying, setDeploying] = useState(false)
  const { toast } = useToast()

  async function checkForUpdates() {
    if (!confirm('Install the latest version of Obsidian now?')) return
    setDeploying(true)
    try {
      const result = await postDeploy()
      toast({ tone: 'ok', title: 'Updated', body: `${result.detail} | ${result.commit}` })
    } catch (err) {
      toast({ tone: 'error', title: 'Update failed', body: err instanceof ApiError ? err.message : 'Something went wrong.' })
    } finally {
      setDeploying(false)
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Updates</h2>
      <p className="settings-sub">{SUB}</p>
      <button className="settings-btn" disabled={deploying} onClick={checkForUpdates}>
        {deploying ? 'Updating…' : 'Check for updates'}
      </button>
      <div className="about-footer">Obsidian</div>
    </div>
  )
}
