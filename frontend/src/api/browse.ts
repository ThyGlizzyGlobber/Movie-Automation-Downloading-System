import { request } from './client'
import type { TmdbListResponse } from '../types/movies'

export type BrowseType = 'movie' | 'tv'
export type BrowseSort = 'popular' | 'trending' | 'newest' | 'rated'
export type BrowseList = 'all' | 'coming-soon'

export interface BrowseParams {
  type: BrowseType
  sort: BrowseSort
  list: BrowseList
  genre: number | null
  provider: number | null
  year: number | null
}

// One page of the browse grid. Trending and Coming Soon come from their
// own endpoints (they are lists, not filters, and take no other
// filter); everything else goes through the combined discover route.
export function browsePage(params: BrowseParams, page: number): Promise<TmdbListResponse> {
  const base = params.type === 'tv' ? '/api/tv/discover' : '/api/discover'
  if (params.list === 'coming-soon') return request<TmdbListResponse>(`${base}/coming-soon?page=${page}`)
  if (params.sort === 'trending') return request<TmdbListResponse>(`${base}/trending?page=${page}`)
  const q = new URLSearchParams({ page: String(page), sort: params.sort })
  if (params.genre) q.set('genre', String(params.genre))
  if (params.provider) q.set('provider', String(params.provider))
  if (params.year) q.set('year', String(params.year))
  return request<TmdbListResponse>(`${base}?${q.toString()}`)
}

// Builds a router path for a set of browse filters; unset filters are
// left out so the URL stays short.
export function browseHref(partial: Partial<BrowseParams> & { type: BrowseType }): string {
  const q = new URLSearchParams()
  q.set('type', partial.type)
  if (partial.list && partial.list !== 'all') q.set('list', partial.list)
  if (partial.sort && partial.sort !== 'popular') q.set('sort', partial.sort)
  if (partial.genre) q.set('genre', String(partial.genre))
  if (partial.provider) q.set('provider', String(partial.provider))
  if (partial.year) q.set('year', String(partial.year))
  return `/browse?${q.toString()}`
}
