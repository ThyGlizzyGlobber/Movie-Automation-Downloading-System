import { postJson, request } from './client'
import type { MovieDetail, MovieTrailer, PersonDetail, TmdbListItem, TmdbListResponse } from '../types/movies'

export function searchMovies(query: string, providerId?: number | null) {
  return postJson<TmdbListItem[]>('/api/search', { query, provider_id: providerId ?? null })
}

export function getDiscoverPopular(page = 1) {
  return request<TmdbListResponse>(`/api/discover/popular?page=${page}`)
}

export function getDiscoverTrending(page = 1) {
  return request<TmdbListResponse>(`/api/discover/trending?page=${page}`)
}

export function getDiscoverProviders(region = 'US') {
  return request<Record<string, unknown>[]>(`/api/discover/providers?region=${region}`)
}

export function getDiscoverByProvider(providerId: number, page = 1, region = 'US') {
  return request<TmdbListResponse>(`/api/discover/providers/${providerId}?page=${page}&region=${region}`)
}

export function getDiscoverByGenre(genreId: number, page = 1, region = 'US') {
  return request<TmdbListResponse>(`/api/discover/genre/${genreId}?page=${page}&region=${region}`)
}

export function getComingSoon(page = 1, region = 'US') {
  return request<TmdbListResponse>(`/api/discover/coming-soon?page=${page}&region=${region}`)
}

export function getMovie(tmdbId: number) {
  return request<MovieDetail>(`/api/movies/${tmdbId}`)
}

// "This copy is broken" for the filed copy of a movie: the library ledger
// (or, for a file Meridian never added, Plex) points at it; it's deleted
// and its release blacklisted for the title.
export function rejectCurrentMovieCopy(tmdbId: number) {
  return request<{ removed: string[] }>(`/api/movies/${tmdbId}/reject-current`, { method: 'POST' })
}

export function getMovieTrailer(tmdbId: number) {
  return request<MovieTrailer>(`/api/movies/${tmdbId}/trailer`)
}

export function getPerson(personId: number) {
  return request<PersonDetail>(`/api/person/${personId}`)
}
