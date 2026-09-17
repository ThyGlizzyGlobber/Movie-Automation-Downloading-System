import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useSession } from '../features/auth/useSession'
import { logout } from '../api/auth'
import type { SessionInfo } from '../types/auth'
import { badgeLabel, initialsOf, useActiveRequestCount } from '../lib/useActiveRequestCount'
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
        <span className="brand-wordmark">Meridian</span>
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
        <button id="searchToggleBtn" aria-label="Search" title={`Search (${isMac ? '⌘K' : 'Ctrl+K'})`} onClick={onOpenSearch}>
          <Icon name="search" />
        </button>
        <AccountMenu session={session.data} initials={initials} settingsActive={settingsActive} />
      </div>
    </div>
  )
}

// The avatar opens a small menu: Account, Settings (admins), Sign out.
function AccountMenu({ session, initials, settingsActive }: { session: SessionInfo | undefined; initials: string; settingsActive: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const location = useLocation()

  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  function go(path: string) {
    setOpen(false)
    navigate(path)
  }
  async function signOut() {
    setOpen(false)
    try {
      await logout()
    } finally {
      queryClient.invalidateQueries({ queryKey: ['session'] })
    }
  }

  const onAccountPage = location.pathname === '/account'
  return (
    <div className="account-menu" ref={ref}>
      <button
        id="accountToggle"
        aria-label="Account menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`${initials || session?.avatar ? 'has-initials' : ''}${session?.avatar ? ' has-picture' : ''}${onAccountPage || settingsActive || open ? ' active' : ''}`}
      >
        <Avatar src="/api/me/avatar" name={session?.username} hasPicture={!!session?.avatar} />
      </button>
      {open && (
        <div className="account-menu-panel" role="menu">
          <div className="account-menu-who">
            <b>{session?.username ?? 'Signed in'}</b>
            <small>{session?.is_admin ? 'Admin' : 'Household'}</small>
          </div>
          <button role="menuitem" className="account-menu-item" onClick={() => go('/account')}>
            <Icon name="user" />
            Account
          </button>
          {session?.is_admin && (
            <button role="menuitem" className="account-menu-item" onClick={() => go('/settings')}>
              <Icon name="gear" />
              Settings
            </button>
          )}
          <button role="menuitem" className="account-menu-item danger" onClick={signOut}>
            <Icon name="close" />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
