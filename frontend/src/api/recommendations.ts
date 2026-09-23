import { request } from './client'
import type { Recommendations } from '../types/recommendations'

// The personalised rows and the day's row order for one landing page.
// `page` decides both which half of the catalogue is drawn from and which
// rows are pinned — see PAGE_LAYOUTS in api.py.
export function getRecommendations(page: 'home' | 'movies' | 'tv') {
  return request<Recommendations>(`/api/recommendations?page=${page}`)
}
