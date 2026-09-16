import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getDiscoverTrending, getMovie, searchMovies } from '../api/movies'
import { getTvDiscoverTrending, getTvShow, searchTv } from '../api/tv'
import { getOnDeck } from '../api/plex'
import { listRequests } from '../api/requests'
import { readRecentSearches } from '../lib/recentSearches'
import PosterCard from './PosterCard'
import type { TmdbListItem } from '../types/movies'

// What the search palette shows before anything is typed: what's
// trending, then one strip that takes turns each time the palette opens:
// titles like what this person is watching on Plex, then like what they
// last searched for (the household's newest request stands in when
// neither exists). Tapping a poster opens it.

type Kind = 'movie' | 'tv'
interface Tagged extends TmdbListItem {
  mediaType: Kind
}
interface Seed {
  kind: Kind
  id: number
  label: string
}

function interleave(movies: TmdbListItem[], shows: TmdbListItem[], count: number): Tagged[] {
  const out: Tagged[] = []
  for (let i = 0; out.length < count && (i < movies.length || i < shows.length); i++) {
    if (movies[i]) out.push({ ...movies[i], mediaType: 'movie' })
    if (shows[i] && out.length < count) out.push({ ...shows[i], mediaType: 'tv' })
  }
  return out
}

async function moreLike(seed: Seed): Promise<Tagged[]> {
  const detail = seed.kind === 'movie' ? await getMovie(seed.id) : await getTvShow(seed.id)
  const results = (detail.recommendations?.results ?? []) as TmdbListItem[]
  return results.slice(0, 12).map((item) => ({ ...item, mediaType: seed.kind }))
}

// The title a search most likely meant: the best-known hit across
// movies and shows for that text.
async function bestHit(query: string): Promise<{ kind: Kind; id: number } | null> {
  const [movies, shows] = await Promise.all([searchMovies(query).catch(() => []), searchTv(query).catch(() => [])])
  const tagged: Tagged[] = [...movies.map((m) => ({ ...m, mediaType: 'movie' as const })), ...shows.map((s) => ({ ...s, mediaType: 'tv' as const }))]
  tagged.sort((a, b) => (b.vote_count ?? 0) - (a.vote_count ?? 0))
  return tagged[0] ? { kind: tagged[0].mediaType, id: tagged[0].id } : null
}

// Which personal seed had the second row last time the palette opened,
// so watching and searching take turns.
let lastShown: string | null = null

function Strip({ label, items }: { label: string; items: Tagged[] }) {
  if (!items.length) return null
  return (
    <div className="search-suggest">
      <span className="search-suggest-label">{label}</span>
      <div className="search-suggest-row">
        {items.map((item) => (
          <PosterCard key={`${item.mediaType}-${item.id}`} item={item} mediaType={item.mediaType} mixed />
        ))}
      </div>
    </div>
  )
}

export default function SearchSuggestions({ onPick }: { onPick: () => void }) {
  const trending = useQuery({
    queryKey: ['search', 'suggest', 'trending'],
    staleTime: 10 * 60_000,
    queryFn: async () => {
      const [movies, shows] = await Promise.all([getDiscoverTrending().catch(() => null), getTvDiscoverTrending().catch(() => null)])
      return interleave(movies?.results ?? [], shows?.results ?? [], 10)
    },
  })

  // Watching: the first Continue Watching item Plex can tie to a TMDB id.
  const onDeck = useQuery({ queryKey: ['on-deck'], queryFn: getOnDeck, staleTime: 60_000 })
  const watching = (onDeck.data?.available ? onDeck.data.items : []).find((i) => i.tmdb_id != null) ?? null
  const watchingSeed: Seed | null = watching
    ? { kind: watching.media_type, id: watching.tmdb_id!, label: `Because you're watching ${watching.show_title || watching.title || 'this'}` }
    : null

  // Searching: the most recent search on this device, resolved to the
  // title it meant. Read once, synchronously, so the pick below never
  // happens against an empty list.
  const [lastSearch] = useState<string | null>(() => readRecentSearches()[0] ?? null)
  const searched = useQuery({
    queryKey: ['search', 'suggest', 'hit', lastSearch],
    enabled: !!lastSearch,
    staleTime: 10 * 60_000,
    queryFn: () => bestHit(lastSearch!),
  })
  const searchSeed: Seed | null = lastSearch && searched.data ? { ...searched.data, label: `Because you searched for ${lastSearch}` } : null

  // Fallback when Plex has nothing on deck and nothing was searched yet.
  const needAsked = (onDeck.isFetched || onDeck.isError) && !watchingSeed && (!lastSearch || (searched.isFetched && !searchSeed))
  const requests = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), staleTime: 30_000, enabled: needAsked })
  const asked = (requests.data ?? []).find((r) => r.media_type === 'movie' || r.media_type === 'episode' || r.media_type === 'pack') ?? null
  const askedSeed: Seed | null = asked ? { kind: asked.media_type === 'movie' ? 'movie' : 'tv', id: asked.tmdb_id, label: `Because you asked for ${asked.title}` } : null

  // Two rows only: trending, then one personal seed — whichever of
  // watching / searching didn't have the row last time. Picked once per
  // open, after both have settled, so the row never switches mid-open.
  const settled = (onDeck.isFetched || onDeck.isError) && (!lastSearch || searched.isFetched || searched.isError) && (!needAsked || requests.isFetched || requests.isError)
  const picked = useRef<Seed | null | undefined>(undefined)
  if (picked.current === undefined && settled) {
    const available = [watchingSeed, searchSeed].filter((s): s is Seed => !!s)
    const pick = available.find((s) => s.label !== lastShown) ?? available[0] ?? askedSeed ?? null
    picked.current = pick
    if (pick) lastShown = pick.label
  }
  const seed = picked.current ?? null
  const personal = useQuery({
    queryKey: ['search', 'suggest', 'like', seed ? `${seed.kind}:${seed.id}` : null],
    enabled: !!seed,
    staleTime: 10 * 60_000,
    queryFn: async () => {
      const items = await moreLike(seed!).catch(() => [] as Tagged[])
      // A title already in the trending row doesn't repeat below it.
      const shown = new Set((trending.data ?? []).map((t) => `${t.mediaType}-${t.id}`))
      return items.filter((item) => !shown.has(`${item.mediaType}-${item.id}`)).slice(0, 10)
    },
  })

  if (!trending.data?.length && !personal.data?.length) return null
  return (
    <div
      className="search-suggestions"
      onClick={(e) => {
        if ((e.target as HTMLElement).closest('a')) onPick()
      }}
    >
      <Strip label="Trending now" items={trending.data ?? []} />
      {seed && <Strip label={seed.label} items={personal.data ?? []} />}
    </div>
  )
}
