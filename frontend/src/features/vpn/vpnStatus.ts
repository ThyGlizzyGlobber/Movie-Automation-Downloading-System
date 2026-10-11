import type { VpnApplied, VpnProvider, VpnStatus } from '../../types/features'

// Where the VPN is at, in words: what's waiting, what's connected, and
// what it means for downloads.
export function vpnSummary(applied: VpnApplied, status: VpnStatus, providers: VpnProvider[]): { tone: 'ok' | 'wait' | 'bad'; text: string } {
  // A WireGuard file's provider has no name of its own here.
  const name = applied.provider === 'custom' ? undefined : providers.find((p) => p.id === applied.provider)?.label
  if (applied.pending || applied.state === 'applying') {
    return { tone: 'wait', text: 'Applying your VPN details. This takes up to a minute.' }
  }
  if (applied.state === 'failed') {
    return { tone: 'bad', text: applied.message ?? "Those VPN details couldn't be applied." }
  }
  if (status.connected) {
    const where = status.country ? ` in ${status.country}` : ''
    return { tone: 'ok', text: `Connected through ${name ?? 'your VPN'}${where} (${status.public_ip}). Downloads go through it.` }
  }
  if (applied.state === 'applied') {
    const recent = applied.at && Date.now() - new Date(applied.at).getTime() < 3 * 60_000
    return recent
      ? { tone: 'wait', text: `Connecting to ${name ?? 'your VPN'}…` }
      : { tone: 'bad', text: `Not connected to ${name ?? 'your VPN'}. Downloads wait until it is. Check your VPN details and account.` }
  }
  return { tone: 'bad', text: "No VPN is set up yet. Downloads can't start until one is." }
}

export function vpnBusy(applied: VpnApplied | undefined, status: VpnStatus | undefined): boolean {
  if (!applied || !status) return false
  return vpnSummary(applied, status, []).tone === 'wait'
}
