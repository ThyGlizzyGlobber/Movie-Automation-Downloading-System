import { createHashRouter, Navigate } from 'react-router-dom'
import AppShell from '../components/AppShell'
import BrowsePage from '../components/BrowsePage'
import AccountPage from '../features/account/AccountPage'
import RequireAdmin from '../features/auth/RequireAdmin'
import MoviesLandingPage from '../features/movies/MoviesLandingPage'
import MovieDetailPage from '../features/movies/MovieDetailPage'
import TvLandingPage from '../features/tv/TvLandingPage'
import ShowDetailPage from '../features/tv/ShowDetailPage'
import WatchingPage from '../features/tv/WatchingPage'
import SearchPage from '../features/search/SearchPage'
import PersonPage from '../features/person/PersonPage'
import RequestsPage from '../features/requests/RequestsPage'
import SettingsPage from '../features/settings/SettingsPage'
import HomePage from '../features/home/HomePage'

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
      { path: 'tv/watching', element: <WatchingPage /> },
      { path: 'tv/:id', element: <ShowDetailPage /> },

      // Every row's "See all" and the genre / service chips land here;
      // the filters live in the query string.
      { path: 'browse', element: <BrowsePage /> },

      { path: 'person/:id', element: <PersonPage /> },
      { path: 'search/:query', element: <SearchPage /> },
      { path: 'requests', element: <RequestsPage /> },
      { path: 'account', element: <AccountPage /> },
      {
        path: 'settings',
        element: (
          <RequireAdmin>
            <SettingsPage />
          </RequireAdmin>
        ),
      },
      { path: '*', element: <Navigate to="/home" replace /> },
    ],
  },
])
