import { postJson, putJson, request } from './client'
import type { SetupMutationResult, SetupStatus } from '../types/auth'

const SETUP_TOKEN_HEADER = 'X-Setup-Token'

// GET /api/setup/status needs no auth/token at all — see api.py's own
// docstring for why (it's what the frontend calls to decide whether to
// show the wizard in the first place). Every mutating call below needs
// the setup token as a header; SetupWizard holds it in memory only, never
// in localStorage/a cookie — see its own component for why.

export function getSetupStatus() {
  return request<SetupStatus>('/api/setup/status')
}

export function setupTmdb(apiKey: string, setupToken: string) {
  return putJson<SetupMutationResult>('/api/setup/tmdb', { api_key: apiKey }, { [SETUP_TOKEN_HEADER]: setupToken })
}

export interface QbtConnectionInput {
  host: string
  port: number
  username?: string
  password?: string
}

export function testQbtConnection(body: QbtConnectionInput, setupToken: string) {
  return postJson<{ reachable: boolean; detail?: string }>(
    '/api/setup/qbittorrent/test',
    body,
    { [SETUP_TOKEN_HEADER]: setupToken },
  )
}

export function setupQbt(body: QbtConnectionInput, setupToken: string) {
  return putJson<SetupMutationResult>('/api/setup/qbittorrent', body, { [SETUP_TOKEN_HEADER]: setupToken })
}
