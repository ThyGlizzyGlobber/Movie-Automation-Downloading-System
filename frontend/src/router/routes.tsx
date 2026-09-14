import { createHashRouter, Navigate } from 'react-router-dom'
import AppShell from '../components/AppShell'
import ComingSoon from '../components/ComingSoon'
import AccountPage from '../features/account/AccountPage'
import RequireAdmin from '../features/auth/RequireAdmin'

// Hash-based (Part A2): frontend/nginx.conf has no SPA-fallback catch-all,
// and the PWA manifest's start_url already works against the hash scheme
// — see the migration plan for why this was chosen over browser-history
// routing. Movies/TV/detail/requests/search/settings content lands in
// later steps; ComingSoon keeps every nav target real in the meantime.
export const router = createHashRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/home" replace /> },
      { path: 'home', element: <ComingSoon label="Home" /> },
      { path: 'movies', element: <ComingSoon label="Movies" /> },
      { path: 'movies/*', element: <ComingSoon label="Movies" /> },
      { path: 'tv', element: <ComingSoon label="TV Shows" /> },
      { path: 'tv/*', element: <ComingSoon label="TV Shows" /> },
      { path: 'person/:id', element: <ComingSoon label="Person" /> },
      { path: 'search/:query', element: <ComingSoon label="Search" /> },
      { path: 'requests', element: <ComingSoon label="Requests" /> },
      { path: 'account', element: <AccountPage /> },
      {
        path: 'settings',
        element: (
          <RequireAdmin>
            <ComingSoon label="Settings" />
          </RequireAdmin>
        ),
      },
      { path: '*', element: <Navigate to="/home" replace /> },
    ],
  },
])
