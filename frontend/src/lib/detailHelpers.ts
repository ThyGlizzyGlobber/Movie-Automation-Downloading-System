import { FALLBACK_REGION } from './regions'
import type { ReleaseDatesResult } from '../types/movies'
import type { ContentRatingEntry } from '../types/tv'

export function yearOf(dateStr?: string | null): string {
  return dateStr && dateStr.length >= 4 ? dateStr.slice(0, 4) : ''
}

// The household's region first, then US, then nothing. The fallback is
// what keeps a rating on screen at all: TMDB's coverage outside the US
// is patchy, and a region with no entry for a title would otherwise show
// a blank where every other title has a chip.
export function certificationOf(movie: { release_dates?: { results?: ReleaseDatesResult[] } }, region: string = FALLBACK_REGION): string {
  const results = movie.release_dates?.results ?? []
  const pick = (code: string) =>
    results.find((r) => r.iso_3166_1 === code)?.release_dates.find((rd) => rd.certification)?.certification || ''
  return pick(region) || pick(FALLBACK_REGION)
}

// TV's own shape for the same idea (content_ratings, not release_dates).
export function tvCertificationOf(show: { content_ratings?: { results?: ContentRatingEntry[] } }, region: string = FALLBACK_REGION): string {
  const results = show.content_ratings?.results ?? []
  const pick = (code: string) => results.find((r) => r.iso_3166_1 === code)?.rating || ''
  return pick(region) || pick(FALLBACK_REGION)
}

// ISO-639-1 -> readable name (TMDB's original_language is just the code,
// e.g. "en"/"ja") — the browser's own Intl API already knows this, no
// hand-maintained lookup table to keep in sync with whatever codes TMDB
// happens to use.
function languageNameOf(code?: string | null): string {
  if (!code) return ''
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) || code
  } catch {
    return code
  }
}

// The Language fact: TMDB's original_language, unless the title's own
// spoken languages say otherwise. The field is hand-entered and wrong
// often enough to matter — The Weight (2026), a US production spoken
// only in English, is filed as Portuguese — while spoken_languages
// comes with the audio listing and almost always holds the original.
// So a code the title doesn't speak at all loses to the first language
// it does. Display only: nothing in the download pipeline reads it.
export function displayLanguageOf(original?: string | null, spoken?: { iso_639_1: string }[] | null): string {
  const codes = (spoken ?? []).map((s) => s.iso_639_1).filter(Boolean)
  const code = original && (codes.length === 0 || codes.includes(original)) ? original : codes[0] ?? original
  return languageNameOf(code)
}

