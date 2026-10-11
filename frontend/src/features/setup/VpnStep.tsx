import { useQuery, useQueryClient } from '@tanstack/react-query'
import { setupGetVpn, setupSaveVpn } from '../../api/vpn'
import VpnForm from '../vpn/VpnForm'
import { vpnBusy, vpnSummary } from '../vpn/vpnStatus'

// The wizard's VPN step, on a box: the buyer's own VPN, which is the only way
// qBittorrent can reach the internet. Saving hands the details to the box's
// updater; the step then shows it applying and connecting.
export default function VpnStep({ setupToken, onDone }: { setupToken: string; onDone: () => void }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['setup-vpn'],
    queryFn: () => setupGetVpn(setupToken),
    staleTime: 0,
    refetchInterval: (q) => {
      const data = q.state.data
      return data?.managed && vpnBusy(data.applied, data.status) ? 3000 : false
    },
    refetchIntervalInBackground: true,
  })
  const data = query.data

  if (!data?.managed) {
    return <p className="setup-step-copy">Checking the box's VPN…</p>
  }
  const summary = vpnSummary(data.applied, data.status, data.providers)
  const saved = data.applied.pending || data.applied.state !== null

  return (
    <>
      <h1 className="setup-step-title">Your VPN</h1>
      <p className="setup-step-copy">
        Downloads only ever go through your own VPN. If it isn't connected, nothing downloads, so your
        internet provider never sees what's downloaded. Enter your VPN account's details below.
      </p>
      {saved && (
        <div className={`vpn-status ${summary.tone}`} role="status">
          <span className="vpn-status-dot" aria-hidden="true" />
          <span>{summary.text}</span>
        </div>
      )}
      {!data.updater_running && (
        <p className="setup-error">The box isn't ready to apply VPN details yet. Give it a minute and reload this page.</p>
      )}
      {(!saved || data.applied.state === 'failed') && (
        <VpnForm
          providers={data.providers}
          variant="setup"
          saveLabel="Save and connect"
          onSave={async (body) => {
            await setupSaveVpn(body, setupToken)
            await queryClient.invalidateQueries({ queryKey: ['setup-vpn'] })
          }}
        />
      )}
      {saved && data.applied.state === 'applied' && (
        <div className="setup-actions">
          <button className="setup-button primary" onClick={onDone}>
            {data.status.connected ? 'Continue' : 'Continue while it connects'}
          </button>
        </div>
      )}
    </>
  )
}
