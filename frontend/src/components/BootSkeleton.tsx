import { useEffect, useRef } from 'react'
import { HashRouter, useLocation } from 'react-router-dom'
import Icon from './Icon'
import { HomeSkeleton } from '../features/home/HomePage'
import { useTopbarHeight } from '../lib/chrome'
import { SECTIONS } from '../lib/sections'
import './Topbar.css'
import './Tabbar.css'

// Pages whose top bar sits see-through over a hero (see useSetHasHero).
const HERO_PATHS = [/^\/(home)?$/, /^\/movies(\/\d+)?$/, /^\/tv(\/\d+)?$/]

// The app while it checks setup and the session: the top bar and tab bar
// as the app draws them, the current section lit, and on Home the page's
// own skeleton, so the first paint is already the page. Other pages show
// their skeletons a moment later, once the app itself is up.
export default function BootSkeleton() {
  return (
    <HashRouter>
      <BootFrame />
    </HashRouter>
  )
}

function BootFrame() {
  const path = useLocation().pathname
  const current = SECTIONS.find((s) => path === s.href || path.startsWith(`${s.href}/`))?.tab ?? (path === '/' ? 'home' : null)
  const topbarRef = useRef<HTMLDivElement>(null)
  useTopbarHeight(topbarRef)

  const hero = HERO_PATHS.some((re) => re.test(path)) && path !== '/tv/watching'
  useEffect(() => {
    document.body.classList.toggle('has-hero', hero)
    return () => document.body.classList.remove('has-hero')
  }, [hero])

  return (
    <>
      <div id="topbar" ref={topbarRef} aria-busy="true">
        <span id="topbarLogo">
          <span className="brand-wordmark">Obsidian</span>
        </span>
        <nav className="top-nav-links">
          {SECTIONS.map((s) => (
            <span key={s.tab} className={`top-nav-link${s.tab === current ? ' active' : ''}`}>
              {s.label}
            </span>
          ))}
        </nav>
        <div className="top-nav-right">
          <span id="searchToggleBtn" aria-hidden="true">
            <Icon name="search" />
          </span>
          <div className="account-menu">
            <span id="accountToggle" className="skel has-initials" aria-hidden="true" />
          </div>
        </div>
      </div>
      <main>{current === 'home' && <HomeSkeleton />}</main>
      <nav id="tabbar" aria-hidden="true">
        {SECTIONS.map((s) => (
          <span key={s.tab} className={`tab${s.tab === current ? ' active' : ''}`}>
            <span className="tab-icon">
              <Icon name={s.icon} />
            </span>
            <span className="tab-label">{s.label}</span>
          </span>
        ))}
        <span className="tab">
          <span className="tab-icon">
            <Icon name="search" />
          </span>
          <span className="tab-label">Search</span>
        </span>
      </nav>
    </>
  )
}
