import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getVpn, saveVpn } from '../../api/vpn'
import { useToast } from '../../lib/toast'
import VpnForm from '../vpn/VpnForm'
import { vpnBusy, vpnSummary } from '../vpn/vpnStatus'
import { SettingsCardSkeleton } from './SettingsSkeleton'

const SUB = 'qBittorrent reaches the internet only through this VPN. When it isn\'t connected, downloads wait.'

// Settings › VPN, on a box (truenas/obsidian-box.yaml). Hidden from the
// Settings list everywhere else.
export default function VpnPanel() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [changing, setChanging] = useState(false)
  const query = useQuery({
    queryKey: ['vpn'],
    queryFn: getVpn,
    staleTime: 0,
    // Quick while a change is being applied and connecting; otherwise now
    // and then, so a dropped tunnel shows.
    refetchInterval: (q) => {
      const data = q.state.data
      return data?.managed && vpnBusy(data.applied, data.status) ? 3000 : 15_000
    },
    refetchIntervalInBackground: true,
  })
  const data = query.data

  if (query.isLoading || !data) return <SettingsCardSkeleton title="VPN" sub={SUB} rows={3} />
  if (!data.managed) {
    return (
      <div className="settings-panel-card">
        <h2>VPN</h2>
        <p className="settings-sub">This Obsidian doesn't have a VPN of its own. qBittorrent's connection is set up wherever qBittorrent runs.</p>
      </div>
    )
  }

  const summary = vpnSummary(data.applied, data.status, data.providers)
  const showForm = changing || (!data.applied.state && !data.applied.pending)

  return (
    <div className="settings-panel-card">
      <h2>VPN</h2>
      <p className="settings-sub">{SUB}</p>
      <div className={`vpn-status ${summary.tone}`} role="status">
        <span className="vpn-status-dot" aria-hidden="true" />
        <span>{summary.text}</span>
      </div>
      {!data.updater_running && (
        <p className="plex-error">The box's updater isn't running, so VPN changes can't be applied right now.</p>
      )}
      {showForm ? (
        <VpnForm
          providers={data.providers}
          variant="settings"
          saveLabel="Save VPN details"
          onSave={async (body) => {
            await saveVpn(body)
            setChanging(false)
            toast({ tone: 'ok', title: 'VPN details saved', body: 'The box applies them within a minute.' })
            await queryClient.invalidateQueries({ queryKey: ['vpn'] })
          }}
        />
      ) : (
        <div className="settings-btn-row">
          <button className="settings-btn secondary sm" disabled={data.applied.pending} onClick={() => setChanging(true)}>
            Change VPN
          </button>
        </div>
      )}
    </div>
  )
}
