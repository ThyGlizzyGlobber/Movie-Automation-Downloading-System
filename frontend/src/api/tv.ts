import { postJson, request } from './client'
import type { TmdbListItem, TmdbListResponse, MovieTrailer } from '../types/movies'
import type { TvDetail } from '../types/tv'
import type { ShowOut } from '../types/shows'
import type { BulkDownloadBody, RequestOut } from '../types/requests'
import type { SeasonEpisodes } from '../types/features'

export function searchTv(query: string, providerId?: number | null) {
  return postJson<TmdbListItem[]>('/api/tv/search', { query, provider_id: providerId ?? null })
}

export function getTvDiscoverPopular(page = 1) {
  return request<TmdbListResponse>(`/api/tv/discover/popular?page=${page}`)
}

export function getTvDiscoverTrending(page = 1) {
  return request<TmdbListResponse>(`/api/tv/discover/trending?page=${page}`)
}

export function getTvDiscoverByProvider(providerId: number, page = 1, region = 'US') {
  return request<TmdbListResponse>(`/api/tv/discover/providers/${providerId}?page=${page}&region=${region}`)
}

export function getTvDiscoverByGenre(genreId: number, page = 1, region = 'US') {
  return request<TmdbListResponse>(`/api/tv/discover/genre/${genreId}?page=${page}&region=${region}`)
}

export function getTvComingSoon(page = 1, region = 'US') {
  return request<TmdbListResponse>(`/api/tv/discover/coming-soon?page=${page}&region=${region}`)
}

export function getTvShow(tmdbId: number) {
  return request<TvDetail>(`/api/tv/${tmdbId}`)
}

export function getTvTrailer(tmdbId: number) {
  return request<MovieTrailer>(`/api/tv/${tmdbId}/trailer`)
}

// -- Show subscriptions --

export function listShows(status?: 'watching' | 'paused') {
  return request<ShowOut[]>(`/api/shows${status ? `?status=${status}` : ''}`)
}

export function createShow(tmdbId: number) {
  return postJson<ShowOut>('/api/shows', { tmdb_id: tmdbId })
}

export function deleteShow(id: number) {
  return request<{ deleted: true }>(`/api/shows/${id}`, { method: 'DELETE' })
}

// Keyed by tmdb_id (not the local show id) — the backend creates a paused
// show record behind the scenes on first use if one doesn't exist yet.
export function bulkDownload(tmdbId: number, body: BulkDownloadBody) {
  return postJson<RequestOut>(`/api/tv/${tmdbId}/bulk-download`, body)
}

// Per-episode status for one season (show page episode list) and a
// single-episode request.
export function getSeasonEpisodes(tmdbId: number, seasonNumber: number) {
  return request<SeasonEpisodes>(`/api/tv/${tmdbId}/season/${seasonNumber}/episodes`)
}

export function requestEpisode(tmdbId: number, seasonNumber: number, episodeNumber: number) {
  return request<RequestOut>(`/api/tv/${tmdbId}/episodes/${seasonNumber}/${episodeNumber}`, { method: 'POST' })
}
