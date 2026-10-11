import { useState } from 'react'
import { ApiError } from '../../api/client'
import type { VpnField, VpnInput, VpnProvider } from '../../types/features'
import './VpnForm.css'

// One form for the setup wizard's VPN step and Settings › VPN. Each takes
// the buyer's own VPN details for their provider: only the fields that
// provider and connection type need, or a WireGuard file for any other.
type Variant = 'setup' | 'settings'
const CLASSES: Record<Variant, { field: string; button: string; error: string; hint: string }> = {
  setup: { field: 'setup-field', button: 'setup-button primary', error: 'setup-error', hint: 'setup-step-copy vpn-hint' },
  settings: { field: 'settings-field', button: 'settings-btn', error: 'plex-error', hint: 'plex-hint' },
}

const LABELS: Record<VpnField, string> = {
  private_key: 'WireGuard private key',
  public_key: "Server's public key",
  preshared_key: 'Pre-shared key (if your provider gives one)',
  addresses: 'WireGuard address',
  endpoint_ip: 'Server address',
  endpoint_port: 'Server port',
  user: 'Username',
  password: 'Password',
}
const SECRET: VpnField[] = ['private_key', 'preshared_key', 'password']
const PLACEHOLDERS: Partial<Record<VpnField, string>> = {
  private_key: '44 characters, ending in =',
  addresses: 'like 10.64.0.2/32',
}
const KINDS = { wireguard: 'WireGuard (faster)', openvpn: 'OpenVPN' } as const

export default function VpnForm({
  providers,
  variant,
  saveLabel,
  onSave,
}: {
  providers: VpnProvider[]
  variant: Variant
  saveLabel: string
  onSave: (body: VpnInput) => Promise<unknown>
}) {
  const css = CLASSES[variant]
  const [providerId, setProviderId] = useState('')
  const [chosenKind, setChosenKind] = useState<keyof typeof KINDS | null>(null)
  const [values, setValues] = useState<Partial<Record<VpnField, string>>>({})
  const [file, setFile] = useState('')
  const [countries, setCountries] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const provider = providers.find((p) => p.id === providerId)
  const kinds = provider ? (Object.keys(provider.types) as (keyof typeof KINDS)[]) : []
  const kind = chosenKind && kinds.includes(chosenKind) ? chosenKind : kinds[0]
  const isFile = providerId === 'custom'
  const fields = !provider || !kind || isFile ? [] : provider.types[kind] ?? []
  const ready = provider && (isFile ? file.trim() : fields.every((f) => f === 'preshared_key' || values[f]?.trim()))

  async function save() {
    if (!provider || !kind) return
    setSaving(true)
    setError(null)
    try {
      const body: VpnInput = isFile
        ? { provider: 'custom', wireguard_file: file, server_countries: countries }
        : { provider: provider.id, type: kind, server_countries: countries, ...Object.fromEntries(fields.map((f) => [f, values[f] ?? ''])) }
      await onSave(body)
      setValues({})
      setFile('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="vpn-form">
      <div className={css.field}>
        <label htmlFor="vpn-provider">VPN provider</label>
        <select id="vpn-provider" value={providerId} onChange={(e) => setProviderId(e.target.value)}>
          <option value="">Choose your provider</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      {kinds.length > 1 && !isFile && (
        <div className={css.field}>
          <label htmlFor="vpn-kind">Connection</label>
          <select id="vpn-kind" value={kind} onChange={(e) => setChosenKind(e.target.value as keyof typeof KINDS)}>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {KINDS[k]}
              </option>
            ))}
          </select>
        </div>
      )}

      {provider && (
        <p className={css.hint}>
          {isFile
            ? 'Download a WireGuard configuration file from your VPN account, open it, and paste everything in it here.'
            : kind === 'wireguard'
              ? `Find these in your ${provider.label} account, under WireGuard or manual setup.`
              : `Use the OpenVPN or "service" username and password from your ${provider.label} account, not the one you sign in with.`}
        </p>
      )}

      {isFile ? (
        <div className={css.field}>
          <label htmlFor="vpn-file">WireGuard file</label>
          <textarea
            id="vpn-file"
            rows={8}
            spellCheck={false}
            autoCapitalize="off"
            value={file}
            onChange={(e) => setFile(e.target.value)}
            placeholder={'[Interface]\nPrivateKey = …\nAddress = …\n\n[Peer]\nPublicKey = …\nEndpoint = …'}
          />
        </div>
      ) : (
        fields.map((field) => (
          <div className={css.field} key={field}>
            <label htmlFor={`vpn-${field}`}>{LABELS[field]}</label>
            <input
              id={`vpn-${field}`}
              type={SECRET.includes(field) ? 'password' : 'text'}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              value={values[field] ?? ''}
              placeholder={PLACEHOLDERS[field]}
              onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
            />
          </div>
        ))
      )}

      {provider && (
        <div className={css.field}>
          <label htmlFor="vpn-countries">Countries (optional)</label>
          <input
            id="vpn-countries"
            type="text"
            autoComplete="off"
            value={countries}
            onChange={(e) => setCountries(e.target.value)}
            placeholder="like Sweden, Switzerland"
          />
        </div>
      )}

      {error && <p className={css.error}>{error}</p>}
      <div className={variant === 'setup' ? 'setup-actions' : 'settings-btn-row'}>
        <button className={css.button} disabled={!ready || saving} onClick={save}>
          {saving ? 'Saving…' : saveLabel}
        </button>
      </div>
    </div>
  )
}
