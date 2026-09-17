import { NavLink } from 'react-router-dom'
import Icon, { type IconName } from './Icon'
import { badgeLabel, useActiveRequestCount } from '../lib/useActiveRequestCount'
import './Tabbar.css'

const TABS: { tab: string; label: string; href: string; icon: IconName }[] = [
  { tab: 'home', label: 'Home', href: '/home', icon: 'home' },
  { tab: 'movies', label: 'Movies', href: '/movies', icon: 'film' },
  { tab: 'tv', label: 'TV Shows', href: '/tv', icon: 'tv' },
  { tab: 'requests', label: 'Requests', href: '/requests', icon: 'download' },
]

// Phone navigation: the four sections plus search.
export default function Tabbar({ onOpenSearch }: { onOpenSearch: () => void }) {
  const active = useActiveRequestCount()
  return (
    <nav id="tabbar">
      {TABS.map((t) => (
        <NavLink key={t.tab} to={t.href} className={({ isActive }) => `tab${isActive ? ' active' : ''}`}>
          <span className="tab-icon">
            <Icon name={t.icon} />
            {t.tab === 'requests' && active > 0 && <span className="tab-badge">{badgeLabel(active)}</span>}
          </span>
          <span className="tab-label">{t.label}</span>
        </NavLink>
      ))}
      <button className="tab" aria-label="Search" onClick={onOpenSearch}>
        <span className="tab-icon">
          <Icon name="search" />
        </span>
        <span className="tab-label">Search</span>
      </button>
    </nav>
  )
}
