import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { ChromeProvider } from '../lib/chrome'
import Topbar from './Topbar'
import Tabbar from './Tabbar'
import DownloadsFab from './DownloadsFab'
import StorageIndicator from './StorageIndicator'
import SearchOverlay from './SearchOverlay'
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

  return (
    <ChromeProvider>
      <Topbar onOpenSearch={() => setSearchOpen(true)} />
      <SearchOverlay isOpen={searchOpen} onClose={() => setSearchOpen(false)} />
      <main>
        <Outlet />
      </main>
      <Tabbar />
      <DownloadsFab />
      <StorageIndicator />
      <TutorialOverlay />
    </ChromeProvider>
  )
}
