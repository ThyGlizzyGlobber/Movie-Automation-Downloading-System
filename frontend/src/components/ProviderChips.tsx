import { Link } from 'react-router-dom'
import { CURATED_PROVIDERS } from '../lib/providers'
import './ProviderChips.css'

// hrefPrefix is a router-relative path (e.g. "/movies/provider"), not a
// literal hash href — this renders react-router Links, consistent with
// MediaRow's row-title links, now that routing exists (it didn't yet
// when this component was first built).
export default function ProviderChips({ hrefPrefix }: { hrefPrefix: string }) {
  return (
    <div className="provider-row">
      {CURATED_PROVIDERS.map((cp) => (
        <Link key={cp.id} to={`${hrefPrefix}/${cp.id}/${encodeURIComponent(cp.name)}`}>
          <div className="provider-chip">
            <img src={`/icons/${cp.icon}`} alt="" />
          </div>
          <div className="provider-label">{cp.name}</div>
        </Link>
      ))}
    </div>
  )
}
