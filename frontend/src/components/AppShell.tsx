import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { ToastProvider } from '../lib/toast'
import Topbar from './Topbar'
import Tabbar from './Tabbar'
import StorageIndicator from './StorageIndicator'
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
      <main>
        <Outlet />
      </main>
      <Tabbar onOpenSearch={() => setSearchOpen(true)} />
      <StorageIndicator />
      <TutorialOverlay />
      <ToastStack />
    </ToastProvider>
  )
}
