export interface CuratedProvider {
  id: number
  name: string
  icon: string
}

// TMDB's watch-provider ids are shared across movies and TV (Netflix is
// always 8, etc.), so the same curated chip list backs both sections —
// only the link prefix differs ("/movies/provider" vs "/tv/provider").
export const CURATED_PROVIDERS: CuratedProvider[] = [
  { id: 8, name: 'Netflix', icon: 'netflix.png' },
  { id: 9, name: 'Prime Video', icon: 'prime-video.png' },
  { id: 337, name: 'Disney+', icon: 'disney-plus.png' },
  { id: 1899, name: 'Max', icon: 'max.png' },
  { id: 15, name: 'Hulu', icon: 'hulu.png' },
  { id: 2, name: 'Apple TV', icon: 'apple-tv.png' },
  // 531 ("Paramount+") is a dead TMDB provider id — Paramount+ was split
  // into tiered entries (Premium/Essential); 2303 is the current Premium
  // one — kept for the provider-browse id even though the logo is now local.
  { id: 2303, name: 'Paramount+', icon: 'paramount-plus.png' },
  { id: 386, name: 'Peacock', icon: 'peacock.png' },
]

// Subset surfaced as inline "Popular on X" rows on the Movies/TV curated
// pages — all 8 as rows would make an already-heavy page noticeably
// heavier; the full list stays reachable via the "Browse a Service" chip
// grid on the same page.
export const ROW_PROVIDERS = CURATED_PROVIDERS.slice(0, 4) // Netflix, Prime Video, Disney+, Max
