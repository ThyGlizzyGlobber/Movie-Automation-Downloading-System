import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useSession } from '../features/auth/useSession'
import { logout } from '../api/auth'
import type { SessionInfo } from '../types/auth'
import { badgeLabel, initialsOf, useActiveRequestCount } from '../lib/useActiveRequestCount'
import Avatar from './Avatar'
import { useTopbarHeight } from '../lib/chrome'
import { SECTIONS } from '../lib/sections'
import './Topbar.css'
import Icon from './Icon'

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

  useTopbarHeight(topbarRef)

  const settingsActive = location.pathname.startsWith('/settings')
  const initials = initialsOf(session.data?.username)

  return (
    <div id="topbar" ref={topbarRef}>
      <NavLink id="topbarLogo" to="/home" aria-label="Obsidian home">
        <span className="brand-wordmark">Obsidian</span>
      </NavLink>
      <nav className="top-nav-links">
        {SECTIONS.map((s) => (
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

// The avatar opens the account sheet: the page blurred behind, the way
// search opens, and a glass panel with who's signed in and a row each
// for Account, Settings (admins) and Sign out. Full width under the bar
// on a phone, where it's the only way to Settings; under the avatar at
// the pill's right end on desktop — one design at every width, where it
// used to be a small dropdown on desktop.
// `null` joins `undefined` here rather than being narrowed away at the
// call site: signed-out is now a value getSession can return, and this
// menu already renders the same way for "no session yet" and "no session
// at all". Collapsing them to one falsy check keeps that true.
function AccountMenu({ session, initials, settingsActive }: { session: SessionInfo | null | undefined; initials: string; settingsActive: boolean }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const location = useLocation()

  // The sheet closes from its own backdrop; Escape closes it too.
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  // The nav pill fades out under the sheet as it does under search.
  useEffect(() => {
    document.body.classList.toggle('account-open', open)
    return () => document.body.classList.remove('account-open')
  }, [open])

  // Leaving the page (a tab bar tap) closes it too.
  useEffect(() => setOpen(false), [location.pathname])

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
    <div className="account-menu">
      <button
        id="accountToggle"
        aria-label="Account menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`${initials || session?.avatar ? 'has-initials' : ''}${session?.avatar ? ' has-picture' : ''}${onAccountPage || settingsActive || open ? ' active' : ''}`}
      >
        <Avatar src="/api/me/avatar" name={session?.username} hasPicture={!!session?.avatar} />
      </button>
      {open &&
        // Portalled: #topbar's backdrop-filter would otherwise make it
        // the containing block of anything fixed inside it.
        createPortal(
          <div
            id="accountOverlay"
            onClick={(e) => {
              if (e.target === e.currentTarget) setOpen(false)
            }}
          >
            <div className="account-sheet" role="menu" aria-label="Account">
              <div className="account-sheet-who">
                <Avatar src="/api/me/avatar" name={session?.username} hasPicture={!!session?.avatar} className="account-avatar-big" />
                <div>
                  <b>{session?.username ?? 'Signed in'}</b>
                  <small>{session?.is_admin ? 'Admin' : 'Household'}</small>
                </div>
              </div>
              <button role="menuitem" className={`account-sheet-item${onAccountPage ? ' on' : ''}`} onClick={() => go('/account')}>
                <span className="account-sheet-icon">
                  <Icon name="user" />
                </span>
                <span>
                  Account
                  <small>Your profile and the tutorial</small>
                </span>
                <Icon name="next" className="account-sheet-go" />
              </button>
              {session?.is_admin && (
                <button role="menuitem" className={`account-sheet-item${settingsActive ? ' on' : ''}`} onClick={() => go('/settings')}>
                  <span className="account-sheet-icon">
                    <Icon name="gear" />
                  </span>
                  <span>
                    Settings
                    <small>Plex, downloads and the household</small>
                  </span>
                  <Icon name="next" className="account-sheet-go" />
                </button>
              )}
              <button role="menuitem" className="account-sheet-item danger" onClick={signOut}>
                <span className="account-sheet-icon">
                  <Icon name="close" />
                </span>
                <span>Sign out</span>
              </button>
            </div>
          </div>,
          document.body,
        )}
    </div>
  )
}
