import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { searchMovies } from '../api/movies'
import { searchTv } from '../api/tv'
import type { TmdbListItem } from '../types/movies'

// Live results for the search palette: the top few movie and TV matches
// for the typed text, debounced, merged by popularity. Each side swallows
// its own failure so one endpoint being down still shows the other.
const MAX_RESULTS = 7
const DEBOUNCE_MS = 220

export interface SearchHit extends TmdbListItem {
  mediaType: 'movie' | 'tv'
}

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return debounced
}

export function useLiveSearch(query: string) {
  const q = useDebounced(query.trim(), DEBOUNCE_MS)
  return useQuery({
    queryKey: ['search', 'live', q],
    enabled: q.length >= 2,
    staleTime: 60_000,
    queryFn: async (): Promise<SearchHit[]> => {
      const [movies, shows] = await Promise.all([searchMovies(q).catch(() => []), searchTv(q).catch(() => [])])
      const tagged: SearchHit[] = [
        ...movies.map((m) => ({ ...m, mediaType: 'movie' as const })),
        ...shows.map((s) => ({ ...s, mediaType: 'tv' as const })),
      ]
      return tagged.sort((a, b) => (b.popularity || 0) - (a.popularity || 0)).slice(0, MAX_RESULTS)
    },
  })
}

export function hitYear(hit: SearchHit): string {
  const d = hit.mediaType === 'tv' ? hit.first_air_date : hit.release_date
  return d ? d.slice(0, 4) : ''
}

export function hitTitle(hit: SearchHit): string {
  return hit.title || hit.name || hit.original_title || hit.original_name || 'Untitled'
}

export function hitHref(hit: SearchHit): string {
  return hit.mediaType === 'tv' ? `/tv/${hit.id}` : `/movies/${hit.id}`
}
