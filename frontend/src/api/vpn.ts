import { putJson, request } from './client'
import type { VpnInput, VpnOverview } from '../types/features'

const SETUP_TOKEN_HEADER = 'X-Setup-Token'

// Settings › VPN (admin) and the setup wizard's VPN step (setup code), both
// in backend/app/api/vpn.py. Saving only leaves the details for the box's
// updater to apply; nothing secret ever comes back.
export function getVpn() {
  return request<VpnOverview>('/api/admin/vpn')
}

export function saveVpn(body: VpnInput) {
  return putJson<{ pending: true }>('/api/admin/vpn', body)
}

export function setupGetVpn(setupToken: string) {
  return request<VpnOverview>('/api/setup/vpn', { headers: { [SETUP_TOKEN_HEADER]: setupToken } })
}

export function setupSaveVpn(body: VpnInput, setupToken: string) {
  return putJson<{ pending: true }>('/api/setup/vpn', body, { [SETUP_TOKEN_HEADER]: setupToken })
}
