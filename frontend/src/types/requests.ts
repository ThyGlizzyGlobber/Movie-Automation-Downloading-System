// Mirrors api.py's RequestOut (api.py:155-174) field-for-field.
export type RequestStatus =
  | 'queued'
  | 'searching'
  | 'downloading'
  | 'complete'
  | 'no qualifying results'
  | 'insufficient free space'
  | 'downloaded, not filed'
  | 'failed'
  | 'cancelled'

export type MediaType = 'movie' | 'episode' | 'pack'

export interface RequestOut {
  id: number
  query: string | null
  tmdb_id: number
  title: string
  release_year: number | null
  status: RequestStatus
  error_message: string | null
  result: Record<string, unknown> | null
  created_at: string
  updated_at: string
  media_type: MediaType
  show_id: number | null
  season_number: number | null
  episode_number: number | null
  season_range_end: number | null
}

// api.py:94-106
export interface CreateRequestBody {
  tmdb_id: number
  query?: string | null
  min_resolution?: string | null
}

// api.py:181-195 — today's real shape: no season-range or min_resolution
// support yet, despite db.py's schema already having columns for both
// (see Part J4/K of the migration plan for the planned extensions).
export interface BulkDownloadBody {
  scope: 'season' | 'series'
  season_number?: number | null
}
