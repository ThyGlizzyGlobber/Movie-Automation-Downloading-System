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
  // The pipeline's audit snapshot: which release won and how it scored.
  // Stays loosely typed because the worker writes several result shapes
  // into it (movie, episode, pack) plus later additions like
  // stall_attempts — `RequestPick` narrows the part the UI reads.
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

/** The winning candidate and its score, as `worker._result_summary`
 *  persists them onto `RequestOut.result`. Every field is optional:
 *  older rows predate parts of the snapshot, and a request that never
 *  got as far as picking anything has no winner at all. */
export interface RequestPick {
  winner?: {
    fileName?: string | null
    engineName?: string | null
    fileSize?: number | null
    // -1 or missing means the plugin reported no seeder count. That is
    // a meaningfully different thing from zero, and the reason this is
    // worth showing: an unreported swarm is the one that strands a
    // download at 0%.
    nbSeeders?: number | null
  } | null
  score?: {
    resolution_score?: number
    source_score?: number
    codec_score?: number
    container_score?: number
    seeder_score?: number
    composite?: number
  } | null
  candidates_considered?: number | null
  variant_used?: string | null
  add_error?: string | null
  stall_attempts?: number | null
}
