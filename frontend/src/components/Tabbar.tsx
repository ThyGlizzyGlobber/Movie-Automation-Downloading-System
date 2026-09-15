import { NavLink } from 'react-router-dom'
import Icon, { type IconName } from './Icon'
import './Tabbar.css'

const TABS: { tab: string; label: string; href: string; icon: IconName }[] = [
  { tab: 'home', label: 'Home', href: '/home', icon: 'home' },
  { tab: 'movies', label: 'Movies', href: '/movies', icon: 'film' },
  { tab: 'tv', label: 'TV Shows', href: '/tv', icon: 'tv' },
]

export default function Tabbar() {
  return (
    <nav id="tabbar">
      {TABS.map((t) => (
        <NavLink key={t.tab} to={t.href} className={({ isActive }) => `tab${isActive ? ' active' : ''}`}>
          <span className="tab-icon">
            <Icon name={t.icon} />
          </span>
          {t.label}
        </NavLink>
      ))}
    </nav>
  )
}
