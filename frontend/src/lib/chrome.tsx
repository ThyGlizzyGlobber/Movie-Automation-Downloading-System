import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

// Ports setChrome({hasHero})'s job from the old app: whether the current
// page wants the topbar's see-through hero treatment instead of its
// normal frosted-bar background. Pages that have a hero (Home, movie/show
// detail — none built yet) call useSetHasHero(true); everything else
// implicitly stays false. A tiny context rather than prop-drilling,
// since Topbar and the page setting it are siblings under AppShell, not
// parent/child.
const ChromeContext = createContext<{ hasHero: boolean; setHasHero: (v: boolean) => void } | null>(null)

export function ChromeProvider({ children }: { children: ReactNode }) {
  const [hasHero, setHasHero] = useState(false)
  return <ChromeContext.Provider value={{ hasHero, setHasHero }}>{children}</ChromeContext.Provider>
}

export function useChrome() {
  const ctx = useContext(ChromeContext)
  if (!ctx) throw new Error('useChrome must be used within ChromeProvider')
  return ctx
}

// A page calls this to opt into the hero topbar treatment for as long as
// it's mounted — resets automatically on unmount/navigation-away.
export function useSetHasHero(value: boolean) {
  const { setHasHero } = useChrome()
  useEffect(() => {
    setHasHero(value)
    return () => setHasHero(false)
  }, [value, setHasHero])
}

// Ports setChrome({title})'s document.title job.
export function usePageTitle(title: string | null) {
  useEffect(() => {
    document.title = title ? `${title} — Smithflix` : 'Smithflix'
    return () => {
      document.title = 'Smithflix'
    }
  }, [title])
}
