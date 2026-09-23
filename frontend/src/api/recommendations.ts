import { request } from './client'
import type { Recommendations } from '../types/recommendations'

// The personalised rows and the day's row order for one landing page.
//
// `page` decides which half of the catalogue is drawn from and which rows
// are pinned — see PAGE_LAYOUTS in api.py. `rows` is the page declaring the
// rows it owns but the backend can't name (the genre rows, keyed by label,
// and the provider rows, keyed by a TMDB provider id), so those get dealt
// with everything else rather than sitting in a fixed tail.
export function getRecommendations(page: 'home' | 'movies' | 'tv', rows = '') {
  const query = new URLSearchParams({ page })
  if (rows) query.set('rows', rows)
  return request<Recommendations>(`/api/recommendations?${query}`)
}
