import { postJson, request } from './client'
import type { Recommendations } from '../types/recommendations'

// Every discovery row on one landing page — personal, named and catalogue
// alike — filled, ranked for the signed-in person and dealt so no title
// repeats, plus the order the whole page runs in. See
// backend/app/api/recommendations.py and feed.py.
export function getRecommendations(page: 'home' | 'movies' | 'tv') {
  return request<Recommendations>(`/api/recommendations?${new URLSearchParams({ page })}`)
}

// Someone opened a title's page: a light taste signal for their own
// recommendations. Fire and forget — it must never hold up the page.
export function recordTitleView(mediaType: 'movie' | 'tv', tmdbId: number, title: string) {
  return postJson<{ recorded: true }>('/api/views', { media_type: mediaType, tmdb_id: tmdbId, title })
}
