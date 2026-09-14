import type { RequestOut } from './requests'

// Mirrors api.py's ShowOut (api.py:206-220).
export interface ShowOut {
  id: number
  tmdb_id: number
  title: string
  status: 'watching' | 'paused'
  created_at: string
  last_checked_at: string | null
  latest_request: RequestOut | null
}
