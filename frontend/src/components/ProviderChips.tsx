import { Link } from 'react-router-dom'
import { CURATED_PROVIDERS } from '../lib/providers'
import { browseHref, type BrowseType } from '../api/browse'
import './ProviderChips.css'

// Each chip opens the browse page filtered to that streaming service.
export default function ProviderChips({ type }: { type: BrowseType }) {
  return (
    <div className="provider-row">
      {CURATED_PROVIDERS.map((cp) => (
        <Link key={cp.id} to={browseHref({ type, provider: cp.id })}>
          <div className="provider-chip">
            <img src={`/icons/${cp.icon}`} alt="" />
          </div>
          <div className="provider-label">{cp.name}</div>
        </Link>
      ))}
    </div>
  )
}
