import { createHashRouter, Navigate } from 'react-router-dom'
import AppShell from '../components/AppShell'
import CategoryPage from '../components/CategoryPage'
import GenreCategoryPage from '../components/GenreCategoryPage'
import ProviderPage from '../components/ProviderPage'
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
import {
  getComingSoon,
  getDiscoverByGenre,
  getDiscoverByProvider,
  getDiscoverPopular,
  getDiscoverTrending,
  searchMovies,
} from '../api/movies'
import {
  getTvComingSoon,
  getTvDiscoverByGenre,
  getTvDiscoverByProvider,
  getTvDiscoverPopular,
  getTvDiscoverTrending,
  searchTv,
} from '../api/tv'

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
      {
        path: 'movies/trending',
        element: (
          <CategoryPage title="Trending Movies" mediaType="movie" fetchPage={getDiscoverTrending} queryKey={['movies', 'trending', 'all']} />
        ),
      },
      {
        path: 'movies/popular',
        element: <CategoryPage title="Popular" mediaType="movie" fetchPage={getDiscoverPopular} queryKey={['movies', 'popular', 'all']} />,
      },
      {
        path: 'movies/coming-soon',
        element: <CategoryPage title="New Releases" mediaType="movie" fetchPage={getComingSoon} queryKey={['movies', 'comingSoon', 'all']} />,
      },
      { path: 'movies/genre/:id/:name', element: <GenreCategoryPage mediaType="movie" fetchByGenre={getDiscoverByGenre} /> },
      {
        path: 'movies/provider/:id/:name',
        element: <ProviderPage mediaType="movie" discoverByProvider={getDiscoverByProvider} search={searchMovies} />,
      },
      { path: 'movies/:id', element: <MovieDetailPage /> },

      { path: 'tv', element: <TvLandingPage /> },
      { path: 'tv/watching', element: <WatchingPage /> },
      {
        path: 'tv/trending',
        element: <CategoryPage title="Trending TV Shows" mediaType="tv" fetchPage={getTvDiscoverTrending} queryKey={['tv', 'trending', 'all']} />,
      },
      {
        path: 'tv/popular',
        element: <CategoryPage title="Popular" mediaType="tv" fetchPage={getTvDiscoverPopular} queryKey={['tv', 'popular', 'all']} />,
      },
      {
        path: 'tv/coming-soon',
        element: <CategoryPage title="Coming Soon" mediaType="tv" fetchPage={getTvComingSoon} queryKey={['tv', 'comingSoon', 'all']} />,
      },
      { path: 'tv/genre/:id/:name', element: <GenreCategoryPage mediaType="tv" fetchByGenre={getTvDiscoverByGenre} /> },
      {
        path: 'tv/provider/:id/:name',
        element: <ProviderPage mediaType="tv" discoverByProvider={getTvDiscoverByProvider} search={searchTv} />,
      },
      { path: 'tv/:id', element: <ShowDetailPage /> },

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
