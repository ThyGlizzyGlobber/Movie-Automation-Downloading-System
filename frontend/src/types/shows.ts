import type { RequestOut } from './requests'

// Mirrors api.py's ShowOut (api.py:206-220).
export interface ShowOut {
  id: number
  tmdb_id: number
  title: string
  status: 'watching' | 'paused'
  created_at: string
  last_checked_at: string | null
  // Part J1 — populated once at subscribe time.
  poster_path: string | null
  // TMDB's status at the last follow check: 'Returning Series' | 'Ended' | 'Canceled' | … | null before any check.
  tmdb_status: string | null
  latest_request: RequestOut | null
}
