// Last few search queries, kept locally per device for the search
// palette's "Recent" chips. Best effort — storage can be unavailable.
const KEY = 'obsidian.recentSearches'
const MAX = 5

export function readRecentSearches(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    const list = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(list) ? list.filter((x): x is string => typeof x === 'string').slice(0, MAX) : []
  } catch {
    return []
  }
}

export function rememberSearch(query: string) {
  const q = query.trim()
  if (!q) return
  try {
    const next = [q, ...readRecentSearches().filter((x) => x.toLowerCase() !== q.toLowerCase())].slice(0, MAX)
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* ignore */
  }
}
