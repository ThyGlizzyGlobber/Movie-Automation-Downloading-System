import { useEffect, useState } from 'react'
import { Outlet, ScrollRestoration } from 'react-router-dom'
import { ToastProvider } from '../lib/toast'
import Topbar from './Topbar'
import Tabbar from './Tabbar'
import SearchOverlay from './SearchOverlay'
import ToastStack from './ToastStack'
import TutorialOverlay from '../features/onboarding/TutorialOverlay'

export default function AppShell() {
  const [searchOpen, setSearchOpen] = useState(false)

  // ⌘K / Ctrl+K opens the search palette from anywhere (the hint on the
  // top bar's search field).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <ToastProvider>
      <Topbar onOpenSearch={() => setSearchOpen(true)} />
      <SearchOverlay isOpen={searchOpen} onClose={() => setSearchOpen(false)} />
      {/* Without this the browser simply keeps whatever scroll offset the
          last page had: click a poster from halfway down Home and the
          content page opens halfway down itself. The document is the
          scroller here (there is no overflow container — see
          HeroCarousel's own window scroll listener), so this is all it
          takes.

          Deliberately this rather than a scrollTo(0, 0) on pathname
          change: it tops out a new navigation *and* puts you back where
          you were on Back, which a blunt reset would throw away — going
          back to Home landing at the top, having lost the row you were
          part-way along, is the same annoyance the other way round. */}
      <ScrollRestoration />
      <main>
        <Outlet />
      </main>
      <Tabbar onOpenSearch={() => setSearchOpen(true)} />
      <TutorialOverlay />
      <ToastStack />
    </ToastProvider>
  )
}
