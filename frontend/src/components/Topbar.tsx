import { useEffect, useRef } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useSession } from '../features/auth/useSession'
import { badgeLabel, initialsOf, useActiveRequestCount } from '../lib/useActiveRequestCount'
import NotificationsMenu from './NotificationsMenu'
import Avatar from './Avatar'
import './Topbar.css'
import Icon from './Icon'

const SECTION_LINKS = [
  { tab: 'home', label: 'Home', href: '/home' },
  { tab: 'movies', label: 'Movies', href: '/movies' },
  { tab: 'tv', label: 'TV Shows', href: '/tv' },
  { tab: 'requests', label: 'Requests', href: '/requests' },
]

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

// The floating glass nav pill from the reference: wordmark, the four
// section links (Requests carries a live badge while anything is still
// downloading), then a search field, the settings gear for admins and
// the account avatar. On phones the links move to the tab bar and the
// search field collapses to a circle.
export default function Topbar({ onOpenSearch }: { onOpenSearch: () => void }) {
  const session = useSession()
  const location = useLocation()
  const navigate = useNavigate()
  const topbarRef = useRef<HTMLDivElement>(null)
  const active = useActiveRequestCount()

  // #topbar is fixed, so it reserves no space in normal flow — main's own
  // top padding stands in for that space instead (see global.css), kept
  // in sync with the topbar's real rendered height via --topbar-h.
  useEffect(() => {
    const el = topbarRef.current
    if (!el) return
    const update = () => document.documentElement.style.setProperty('--topbar-h', `${el.offsetHeight}px`)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    if (document.fonts?.ready) document.fonts.ready.then(update)
    return () => observer.disconnect()
  }, [])

  const settingsActive = location.pathname.startsWith('/settings')
  const initials = initialsOf(session.data?.username)

  return (
    <div id="topbar" ref={topbarRef}>
      <NavLink id="topbarLogo" to="/home" aria-label="Meridian home">
        <span className="brand-wordmark">
          <img className="brand-mark" src="/brand-icon.svg" alt="" />
          Meridian
        </span>
      </NavLink>
      <nav className="top-nav-links">
        {SECTION_LINKS.map((s) => (
          <NavLink
            key={s.tab}
            to={s.href}
            className={({ isActive }) => `top-nav-link${isActive ? ' active' : ''}`}
          >
            {s.label}
            {s.tab === 'requests' && active > 0 && <span className="top-nav-badge">{badgeLabel(active)}</span>}
          </NavLink>
        ))}
      </nav>
      <div className="top-nav-right">
        <button id="searchToggleBtn" aria-label="Search" onClick={onOpenSearch}>
          <Icon name="search" />
          <span className="top-search-label">Search</span>
          <kbd className="top-search-kbd">{isMac ? '⌘K' : 'Ctrl K'}</kbd>
        </button>
        <NotificationsMenu />
        {session.data?.is_admin && (
          <button
            id="settingsToggle"
            aria-label="Settings"
            onClick={() => navigate('/settings')}
            className={settingsActive ? 'active' : undefined}
          >
            <Icon name="gear" />
          </button>
        )}
        <button
          id="accountToggle"
          aria-label="Account"
          onClick={() => navigate('/account')}
          className={`${initials || session.data?.avatar ? 'has-initials' : ''}${session.data?.avatar ? ' has-picture' : ''}${location.pathname === '/account' ? ' active' : ''}`}
        >
          <Avatar src="/api/me/avatar" name={session.data?.username} hasPicture={!!session.data?.avatar} />
        </button>
      </div>
    </div>
  )
}
