import { CURATED_PROVIDERS } from '../lib/providers'
import './ProviderChips.css'

export default function ProviderChips({ hrefPrefix }: { hrefPrefix: string }) {
  return (
    <div className="provider-row">
      {CURATED_PROVIDERS.map((cp) => (
        <a key={cp.id} href={`${hrefPrefix}/${cp.id}/${encodeURIComponent(cp.name)}`}>
          <div className="provider-chip">
            <img src={`/icons/${cp.icon}`} alt="" />
          </div>
          <div className="provider-label">{cp.name}</div>
        </a>
      ))}
    </div>
  )
}
