import { NavLink } from 'react-router-dom'
import Icon from './Icon'
import { SECTIONS } from '../lib/sections'
import { badgeLabel, useActiveRequestCount } from '../lib/useActiveRequestCount'
import './Tabbar.css'

// Phone navigation: the four sections plus search.
export default function Tabbar({ onOpenSearch }: { onOpenSearch: () => void }) {
  const active = useActiveRequestCount()
  return (
    <nav id="tabbar">
      {SECTIONS.map((t) => (
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
