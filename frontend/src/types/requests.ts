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

export type RedownloadMode = 'upgrade' | 'overwrite'

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
  requested_by_plex_id: string | null
  requested_by_username: string | null
  // Part K1/K3 — "upgrade"/"overwrite"/null, drives the queue's
  // "Redownload" tag.
  redownload_mode: RedownloadMode | null
  // Part J1 — denormalized at creation time, null for rows created before
  // this existed or when TMDB has no poster on file.
  poster_path: string | null
  // Part J2 — qBittorrent's live progress fraction (0.0-1.0), refreshed
  // on every download-watcher poll while status === 'downloading'; null
  // before the first poll and for any row never in that status.
  download_progress: number | null
}

// api.py:271-291
export interface CreateRequestBody {
  tmdb_id: number
  query?: string | null
  // Set only from the "Already on Plex" confirmation modal (Part K1).
  // "overwrite" is rejected server-side unless this tmdb_id has a request
  // this app itself organized on record.
  redownload_mode?: RedownloadMode | null
}

// api.py:378-396
export interface BulkDownloadBody {
  scope: 'season' | 'series'
  season_number?: number | null
  redownload_mode?: RedownloadMode | null
}
