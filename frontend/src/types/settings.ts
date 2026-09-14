// GET /api/settings/pipeline returns asdict(resolve_pipeline_settings(store))
// — a fully-resolved dataclass (config.py defaults filled in), same field
// names as PipelineSettingsIn below but never null. Field names confirmed
// against api.py:113-133; exact resolved-dataclass shape (pipeline_settings.py)
// wasn't read for this port — verify against a live GET if these don't match.
export interface PipelineSettings {
  category: string
  min_resolution: string
  min_size_gb: number
  max_size_gb: number
  language_allowlist: string[]
  language_blocklist: string[]
  language_required: string[]
}

// api.py:113-133 — always send the full desired state; null resets that
// field to the config.py default.
export interface PipelineSettingsBody {
  category?: string | null
  min_resolution?: string | null
  min_size_gb?: number | null
  max_size_gb?: number | null
  language_allowlist?: string[] | null
  language_blocklist?: string[] | null
  language_required?: string[] | null
}

// GET /api/settings/tv returns asdict(resolve_tv_settings(store)) — same
// resolved-vs-input relationship as PipelineSettings/PipelineSettingsBody
// above; field names confirmed against api.py:142-152.
export interface TvScheduleSettings {
  show_check_interval_hours: number
  episode_recheck_enabled: boolean
  episode_recheck_interval_hours: number
  episode_recheck_max_attempts: number
  episode_air_buffer_hours: number
}

export interface TvScheduleSettingsBody {
  show_check_interval_hours?: number | null
  episode_recheck_enabled?: boolean | null
  episode_recheck_interval_hours?: number | null
  episode_recheck_max_attempts?: number | null
  episode_air_buffer_hours?: number | null
}

export interface RetentionSettings {
  days: number | null
}

export interface PlexStatus {
  linked: boolean
  username: string | null
  server_name: string | null
  pending: boolean
  error: string | null
}

// PUT /api/settings/tmdb and /api/settings/qbittorrent (Part I's
// Connections panel) — same response shape as their one-time /api/setup/*
// counterparts, just always admin-gated instead of setup-token-gated.
export interface TmdbSettingsResult {
  tmdb_configured: boolean
  tmdb_source: 'db'
  restart_required: true
}

export interface QbtSettingsResult {
  qbt_configured: boolean
  qbt_source: 'db'
  restart_required: true
}

// GET /api/admin/activity (Part D) — mirrors api.py's RequesterStat/
// ActivityOut.
export interface RequesterStat {
  plex_user_id: string
  username: string | null
  total_requests: number
  requests_this_month: number
}

export interface ActivityOut {
  requests: import('./requests').RequestOut[]
  total: number
  user_stats: RequesterStat[]
}
