import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient } from '@tanstack/react-query'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
// Self-hosted, see tokens.css. Mono is only Settings' paths and hosts, so
// just the two weights it uses.
import '@fontsource-variable/outfit'
import '@fontsource-variable/manrope'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import './styles/global.css'
import App from './App.tsx'
import { getHeroSlides } from './api/hero'
import { getRecommendations } from './api/recommendations'
import { persistOptions } from './lib/queryPersistence'

// Most of what the app shows (TMDB lists, details, settings) changes on
// the order of hours, and every write invalidates what it touched. The
// default staleTime of 0 refetched all of it on every mount and every
// return to the tab. Anything live polls on its own interval.
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5 * 60_000 } } })
const persisted = persistOptions(queryClient)

// Opening on Home, its two slowest asks — the hero, whose poster is the
// first thing anyone sees, and the dealt rows — start now, alongside the
// setup and session checks, rather than once both of those have answered
// and the page has mounted: on a phone that was two round trips through
// the tunnel before either was even asked. Same keys and fetchers as
// HomePage's own queries, so it simply finds them answered. Signed out,
// they come back 401 and are asked again when Home mounts after sign-in.
const bootPath = window.location.hash.replace(/^#/, '').split('?')[0]
if (bootPath === '' || bootPath === '/' || bootPath === '/home') {
  void queryClient.prefetchQuery({ queryKey: ['hero', 'home'], queryFn: () => getHeroSlides('home') })
  void queryClient.prefetchQuery({ queryKey: ['recommendations', 'home'], queryFn: () => getRecommendations('home') })
}

// A deploy rebuilds dist/ in place, so a tab left open across one asks for
// page chunks that no longer exist. Reloading picks up the new build.
window.addEventListener('vite:preloadError', () => window.location.reload())

// Earlier builds registered a service worker for Web Push; clear it.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker
    .getRegistrations()
    .then((regs) => regs.forEach((reg) => reg.unregister()))
    .catch(() => {})
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PersistQueryClientProvider client={queryClient} persistOptions={persisted}>
      <App />
    </PersistQueryClientProvider>
  </StrictMode>,
)
