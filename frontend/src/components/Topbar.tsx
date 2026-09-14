import { useEffect, useRef } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useSession } from '../features/auth/useSession'
import './Topbar.css'

const SECTION_LINKS = [
  { tab: 'home', label: 'Home', href: '/home' },
  { tab: 'movies', label: 'Movies', href: '/movies' },
  { tab: 'tv', label: 'TV Shows', href: '/tv' },
]

export default function Topbar({ onOpenSearch }: { onOpenSearch: () => void }) {
  const session = useSession()
  const location = useLocation()
  const navigate = useNavigate()
  const topbarRef = useRef<HTMLDivElement>(null)

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

  return (
    <div id="topbar" ref={topbarRef}>
      <NavLink id="topbarLogo" to="/home" aria-label="Smithflix home">
        <span className="brand-wordmark">
          <span className="brand-word-smith">smith</span>
          <span className="brand-word-flix">flix</span>
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
          </NavLink>
        ))}
      </nav>
      <button id="searchToggleBtn" aria-label="Search" aria-expanded="false" onClick={onOpenSearch}>
        <span className="material-symbols-rounded">search</span>
      </button>
      <button
        id="accountToggle"
        aria-label="Account"
        onClick={() => navigate('/account')}
        className={location.pathname === '/account' ? 'active' : undefined}
      >
        <span className="material-symbols-rounded">account_circle</span>
      </button>
      {session.data?.is_admin && (
        <button
          id="settingsToggle"
          aria-label="Settings"
          onClick={() => navigate('/settings')}
          className={settingsActive ? 'active' : undefined}
        >
          <span className="material-symbols-rounded">settings</span>
        </button>
      )}
    </div>
  )
}
