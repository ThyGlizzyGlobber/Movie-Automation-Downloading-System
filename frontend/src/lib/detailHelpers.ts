import type { Genre, MovieDetail } from '../types/movies'
import type { TvDetail } from '../types/tv'

export function yearOf(dateStr?: string | null): string {
  return dateStr && dateStr.length >= 4 ? dateStr.slice(0, 4) : ''
}

export function certificationOf(movie: MovieDetail): string {
  const usEntry = movie.release_dates?.results?.find((r) => r.iso_3166_1 === 'US')
  if (!usEntry) return ''
  const withCert = usEntry.release_dates.find((rd) => rd.certification)
  return withCert?.certification ?? ''
}

// TV's own shape for the same idea (content_ratings, not release_dates).
export function tvCertificationOf(show: TvDetail): string {
  const usEntry = show.content_ratings?.results?.find((r) => r.iso_3166_1 === 'US')
  return usEntry?.rating ?? ''
}

// ISO-639-1 -> readable name (TMDB's original_language is just the code,
// e.g. "en"/"ja") — the browser's own Intl API already knows this, no
// hand-maintained lookup table to keep in sync with whatever codes TMDB
// happens to use.
export function languageNameOf(code?: string | null): string {
  if (!code) return ''
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) || code
  } catch {
    return code
  }
}

// "Drama, Mystery, and Sci-Fi & Fantasy" — TMDB's own genre-line style.
export function genreLine(genres?: Genre[] | null): string {
  const names = (genres || []).map((g) => g.name)
  if (names.length <= 1) return names[0] || ''
  if (names.length === 2) return `${names[0]} and ${names[1]}`
  return `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`
}

export function statusFollowupText(status: string, errorMessage?: string | null): string {
  switch (status) {
    case 'downloading':
      return "It's on its way — check My Requests for progress."
    case 'complete':
      return 'Done — it should show up in Plex shortly.'
    case 'no qualifying results':
      return errorMessage || 'Nothing that meets our quality bar was found.'
    case 'insufficient free space':
      return "Found a match, but there's not enough space to add it."
    case 'failed':
      return 'Something went wrong — see My Requests.'
    default:
      return "We'll grab the best copy automatically."
  }
}
