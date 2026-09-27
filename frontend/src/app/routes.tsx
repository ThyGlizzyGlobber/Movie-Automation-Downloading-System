import type { ComponentType } from 'react'
import { createHashRouter, Navigate } from 'react-router-dom'
import AppShell from './AppShell'
import RequireAdmin from '../features/auth/RequireAdmin'
import MoviesLandingPage from '../features/movies/MoviesLandingPage'
import MovieDetailPage from '../features/movies/MovieDetailPage'
import TvLandingPage from '../features/tv/TvLandingPage'
import ShowDetailPage from '../features/tv/ShowDetailPage'
import HomePage from '../features/home/HomePage'

// Home, the two landing pages and the detail pages are the everyday path
// and ship in the main bundle. Everything else is its own chunk, fetched
// on first visit; the router holds the old page until it arrives.
function page(load: () => Promise<{ default: ComponentType }>) {
  return async () => ({ Component: (await load()).default })
}

// Hash-based (Part A2): frontend/nginx.conf has no SPA-fallback catch-all,
// and the PWA manifest's start_url already works against the hash scheme
// — see the migration plan for why this was chosen over browser-history
// routing.
export const router = createHashRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/home" replace /> },
      { path: 'home', element: <HomePage /> },

      { path: 'movies', element: <MoviesLandingPage /> },
      { path: 'movies/:id', element: <MovieDetailPage /> },

      { path: 'tv', element: <TvLandingPage /> },
      { path: 'tv/watching', lazy: page(() => import('../features/tv/WatchingPage')) },
      { path: 'tv/:id', element: <ShowDetailPage /> },

      // Every row's "See all" and the genre / service chips land here;
      // the filters live in the query string.
      { path: 'browse', lazy: page(() => import('../features/browse/BrowsePage')) },

      { path: 'person/:id', lazy: page(() => import('../features/person/PersonPage')) },
      { path: 'search/:query', lazy: page(() => import('../features/search/SearchPage')) },
      { path: 'requests', lazy: page(() => import('../features/requests/RequestsPage')) },
      { path: 'account', lazy: page(() => import('../features/account/AccountPage')) },
      {
        path: 'settings',
        lazy: async () => {
          const { default: SettingsPage } = await import('../features/settings/SettingsPage')
          return {
            element: (
              <RequireAdmin>
                <SettingsPage />
              </RequireAdmin>
            ),
          }
        },
      },
      { path: '*', element: <Navigate to="/home" replace /> },
    ],
  },
])
