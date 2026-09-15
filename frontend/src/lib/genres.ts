// TMDB's genre catalogues. The ids are stable, so they are kept here
// rather than fetched on every browse-page visit.
export interface GenreOption {
  id: number
  name: string
}

export const MOVIE_GENRES: GenreOption[] = [
  { id: 28, name: 'Action' },
  { id: 12, name: 'Adventure' },
  { id: 16, name: 'Animation' },
  { id: 35, name: 'Comedy' },
  { id: 80, name: 'Crime' },
  { id: 99, name: 'Documentary' },
  { id: 18, name: 'Drama' },
  { id: 10751, name: 'Family' },
  { id: 14, name: 'Fantasy' },
  { id: 36, name: 'History' },
  { id: 27, name: 'Horror' },
  { id: 10402, name: 'Music' },
  { id: 9648, name: 'Mystery' },
  { id: 10749, name: 'Romance' },
  { id: 878, name: 'Sci-Fi' },
  { id: 53, name: 'Thriller' },
  { id: 10752, name: 'War' },
  { id: 37, name: 'Western' },
]

export const TV_GENRES: GenreOption[] = [
  { id: 10759, name: 'Action & Adventure' },
  { id: 16, name: 'Animation' },
  { id: 35, name: 'Comedy' },
  { id: 80, name: 'Crime' },
  { id: 99, name: 'Documentary' },
  { id: 18, name: 'Drama' },
  { id: 10751, name: 'Family' },
  { id: 10762, name: 'Kids' },
  { id: 9648, name: 'Mystery' },
  { id: 10764, name: 'Reality' },
  { id: 10765, name: 'Sci-Fi & Fantasy' },
  { id: 10766, name: 'Soap' },
  { id: 10767, name: 'Talk' },
  { id: 10768, name: 'War & Politics' },
  { id: 37, name: 'Western' },
]

export function genresFor(type: 'movie' | 'tv'): GenreOption[] {
  return type === 'movie' ? MOVIE_GENRES : TV_GENRES
}

// Movie and TV genres share most ids but not all (Sci-Fi is 878 for
// movies and 10765 for TV). Switching the browse page between the two
// keeps the selection when a counterpart exists.
const COUNTERPARTS: Record<string, number> = {
  'movie:28': 10759,
  'movie:12': 10759,
  'movie:878': 10765,
  'movie:14': 10765,
  'movie:10752': 10768,
  'tv:10759': 28,
  'tv:10765': 878,
  'tv:10768': 10752,
}

export function counterpartGenre(from: 'movie' | 'tv', id: number): number | null {
  const to = from === 'movie' ? 'tv' : 'movie'
  const mapped = COUNTERPARTS[`${from}:${id}`]
  if (mapped !== undefined) return mapped
  return genresFor(to).some((g) => g.id === id) ? id : null
}
