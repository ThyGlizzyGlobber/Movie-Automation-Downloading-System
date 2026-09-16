import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { ChromeProvider } from '../lib/chrome'
import { ToastProvider } from '../lib/toast'
import Topbar from './Topbar'
import Tabbar from './Tabbar'
import StorageIndicator from './StorageIndicator'
import SearchOverlay from './SearchOverlay'
import ToastStack from './ToastStack'
import TutorialOverlay from '../features/onboarding/TutorialOverlay'

export default function AppShell() {
  const [searchOpen, setSearchOpen] = useState(false)

  // Fades the header in to a flatter, more opaque fill as soon as the
  // page scrolls at all — see Topbar.css's body.has-hero.scrolled rule.
  // A small threshold (not a flat scrollY>0) avoids flickering on
  // trackpad rubber-banding right at the top.
  useEffect(() => {
    function update() {
      document.body.classList.toggle('scrolled', window.scrollY > 4)
    }
    window.addEventListener('scroll', update, { passive: true })
    return () => window.removeEventListener('scroll', update)
  }, [])

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
    <ChromeProvider>
      <ToastProvider>
        <Topbar onOpenSearch={() => setSearchOpen(true)} />
        <SearchOverlay isOpen={searchOpen} onClose={() => setSearchOpen(false)} />
        <main>
          <Outlet />
        </main>
        <Tabbar onOpenSearch={() => setSearchOpen(true)} />
        <StorageIndicator />
        <TutorialOverlay />
        <ToastStack />
      </ToastProvider>
    </ChromeProvider>
  )
}
