import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './styles/global.css'
import App from './App.tsx'

// Most of what the app shows (TMDB lists, details, settings) changes on
// the order of hours, and every write invalidates what it touched. The
// default staleTime of 0 refetched all of it on every mount and every
// return to the tab. Anything live polls on its own interval.
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5 * 60_000 } } })

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
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
