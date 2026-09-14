import { NavLink } from 'react-router-dom'
import './Tabbar.css'

const TABS = [
  { tab: 'home', label: 'Home', href: '/home', icon: 'home' },
  { tab: 'movies', label: 'Movies', href: '/movies', icon: 'movie' },
  { tab: 'tv', label: 'TV Shows', href: '/tv', icon: 'tv' },
]

export default function Tabbar() {
  return (
    <nav id="tabbar">
      {TABS.map((t) => (
        <NavLink key={t.tab} to={t.href} className={({ isActive }) => `tab${isActive ? ' active' : ''}`}>
          <span className="icon">
            <span className="material-symbols-rounded">{t.icon}</span>
          </span>
          {t.label}
        </NavLink>
      ))}
    </nav>
  )
}
