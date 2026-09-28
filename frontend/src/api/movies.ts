import { postJson, request } from './client'
import type { MovieDetail, MovieTrailer, PersonDetail, TmdbListItem, TmdbListResponse } from '../types/movies'

export function searchMovies(query: string, providerId?: number | null) {
  return postJson<TmdbListItem[]>('/api/search', { query, provider_id: providerId ?? null })
}

export function getDiscoverTrending(page = 1) {
  return request<TmdbListResponse>(`/api/discover/trending?page=${page}`)
}

export function getMovie(tmdbId: number) {
  return request<MovieDetail>(`/api/movies/${tmdbId}`)
}

// "This copy is broken" for the filed copy of a movie: the library ledger
// (or, for a file Obsidian never added, Plex) points at it; it's deleted
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
