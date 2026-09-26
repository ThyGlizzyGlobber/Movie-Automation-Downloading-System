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

// Detail-page service icon (sits left of the score ring) — a separate
// lookup from CURATED_PROVIDERS above: that one is keyed by TMDB's
// watch-provider ids and drives the home page's own chips; this one
// matches by name against TV's own `networks` field or a movie's
// `production_companies`/`watch/providers`, TMDB's closest signal for
// which service actually originated the title. Name-based, not id-based
// — a network/company id can't be verified without a live TMDB lookup.
// Reuses CURATED_PROVIDERS' own plain app-icon files rather than a
// separate asset set.
export const ORIGINAL_SERVICE_MATCH: { names: string[]; icon: string; label: string }[] = [
  { names: ['Netflix'], icon: 'netflix.png', label: 'Netflix Original' },
  { names: ['Prime Video', 'Amazon Studios', 'Amazon MGM Studios'], icon: 'prime-video.png', label: 'Prime Video Original' },
  { names: ['Apple TV', 'Apple TV+', 'Apple TV Plus', 'Apple Original Films', 'Apple Studios'], icon: 'apple-tv.png', label: 'Apple TV+ Original' },
  { names: ['HBO', 'HBO Max', 'Max'], icon: 'max.png', label: 'Max Original' },
  { names: ['Disney+', 'Disney Plus'], icon: 'disney-plus.png', label: 'Disney+ Original' },
  { names: ['Hulu'], icon: 'hulu.png', label: 'Hulu Original' },
  { names: ['Paramount+', 'Paramount Plus'], icon: 'paramount-plus.png', label: 'Paramount+ Original' },
  // Above Peacock, and the order is doing work: this list is a priority
  // list, since `find` takes the first entry that matches and a show can
  // name two networks. Wolf Like Me is on TMDB as ['Stan', 'Peacock'] —
  // an Australian original with a US co-producer — and for this
  // household Stan is the answer. Checked live 2026-09-26: Bump,
  // Scrublands, Bad Behaviour and C*A*U*G*H*T all report the network as
  // exactly "Stan".
  { names: ['Stan'], icon: 'stan.png', label: 'Stan Original' },
  { names: ['Peacock'], icon: 'peacock.png', label: 'Peacock Original' },
]

interface OriginalServiceSource {
  networks?: { name: string }[]
  production_companies?: { name: string }[]
  'watch/providers'?: { results?: { US?: { flatrate?: { provider_name: string }[] } } }
}

// isTv reads `item.networks` (TV's own field) — reliable on its own. A
// movie has no such field; `production_companies` is the obvious proxy
// but it's the CREDITED production company, not necessarily the streamer,
// so watch/providers' US flatrate list is checked as a fallback for
// movies specifically. First match wins.
export function originalServiceMatch(item: OriginalServiceSource, isTv: boolean) {
  const names = new Set(((isTv ? item.networks : item.production_companies) || []).map((s) => s.name))
  let match = ORIGINAL_SERVICE_MATCH.find((b) => b.names.some((n) => names.has(n)))
  if (!match && !isTv) {
    const flatrate = item['watch/providers']?.results?.US?.flatrate || []
    const providerNames = new Set(flatrate.map((p) => p.provider_name))
    match = ORIGINAL_SERVICE_MATCH.find((b) => b.names.some((n) => providerNames.has(n)))
  }
  return match ?? null
}
