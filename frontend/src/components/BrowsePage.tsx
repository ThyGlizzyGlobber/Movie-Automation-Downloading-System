import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import InfiniteGrid from './InfiniteGrid'
import Icon from './Icon'
import { browsePage, type BrowseList, type BrowseParams, type BrowseSort, type BrowseType } from '../api/browse'
import { listRequests } from '../api/requests'
import { CURATED_PROVIDERS } from '../lib/providers'
import { counterpartGenre, genresFor } from '../lib/genres'
import { usePageTitle } from '../lib/chrome'
import type { TmdbListItem } from '../types/movies'
import './BrowsePage.css'

type Availability = 'all' | 'plex' | 'requested' | 'missing'

const SORTS: { id: BrowseSort; label: string }[] = [
  { id: 'popular', label: 'Popular' },
  { id: 'trending', label: 'Trending' },
  { id: 'newest', label: 'Newest' },
  { id: 'rated', label: 'Top rated' },
]
const AVAILABILITY: { id: Availability; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'plex', label: 'On Plex' },
  { id: 'requested', label: 'On the way' },
  { id: 'missing', label: 'Not yet' },
]
const THIS_YEAR = new Date().getFullYear()
const YEARS = Array.from({ length: THIS_YEAR - 1959 }, (_, i) => THIS_YEAR - i)

function readParams(sp: URLSearchParams): BrowseParams & { avail: Availability } {
  const type: BrowseType = sp.get('type') === 'tv' ? 'tv' : 'movie'
  const sortRaw = sp.get('sort')
  const sort = SORTS.some((s) => s.id === sortRaw) ? (sortRaw as BrowseSort) : 'popular'
  const list: BrowseList = sp.get('list') === 'coming-soon' ? 'coming-soon' : 'all'
  const availRaw = sp.get('avail')
  const avail = AVAILABILITY.some((a) => a.id === availRaw) ? (availRaw as Availability) : 'all'
  const num = (key: string) => {
    const n = Number(sp.get(key))
    return Number.isFinite(n) && n > 0 ? n : null
  }
  return { type, sort, list, genre: num('genre'), provider: num('provider'), year: num('year'), avail }
}

// The browse page from the reference: a title with the result count, an
// availability switch, sort / year / service pickers, a row of genre
// chips and the grid. Every choice lives in the URL so a row's "See all"
// is just a link here and the back button restores the filters.
export default function BrowsePage() {
  const [sp, setSp] = useSearchParams()
  const params = useMemo(() => readParams(sp), [sp])
  const { type, sort, list, genre, provider, year, avail } = params
  const typeLabel = type === 'tv' ? 'TV Shows' : 'Movies'
  usePageTitle(typeLabel)

  // Trending and Coming Soon are fixed lists, so the discover filters are
  // hidden while one of them is showing.
  const filtersApply = list === 'all' && sort !== 'trending'

  function update(changes: Record<string, string | number | null>) {
    const next = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === '' || v === 0) next.delete(k)
      else next.set(k, String(v))
    }
    setSp(next)
  }

  function switchType(to: BrowseType) {
    if (to === type) return
    update({ type: to, genre: genre ? counterpartGenre(type, genre) : null })
  }

  const requests = useQuery({ queryKey: ['requests'], queryFn: () => listRequests(), refetchInterval: 5000 })
  const requestedIds = useMemo(() => {
    const ids = new Set<number>()
    for (const r of requests.data ?? []) {
      if (r.status === 'cancelled') continue
      const isTv = r.media_type !== 'movie'
      if ((type === 'tv') === isTv) ids.add(r.tmdb_id)
    }
    return ids
  }, [requests.data, type])

  const filterItem = useMemo(() => {
    if (avail === 'all') return undefined
    return (item: TmdbListItem) => {
      const requested = requestedIds.has(item.id)
      if (avail === 'plex') return item.on_plex
      if (avail === 'requested') return requested && !item.on_plex
      return !item.on_plex && !requested
    }
  }, [avail, requestedIds])

  const queryKey = ['browse', type, list, sort, genre, provider, year]
  const gridKey = queryKey.join('|')
  // TMDB's total for the current list, shown next to the title.
  const [total, setTotal] = useState<number | null>(null)
  useEffect(() => setTotal(null), [gridKey])
  const onTotal = useCallback((n: number) => setTotal(n), [])
  const genres = genresFor(type)
  const activeGenreName = genres.find((g) => g.id === genre)?.name
  const activeProviderName = CURATED_PROVIDERS.find((p) => p.id === provider)?.name

  const subtitleBits = [
    list === 'coming-soon' ? 'Coming soon' : SORTS.find((s) => s.id === sort)?.label,
    activeGenreName,
    activeProviderName,
    year ? String(year) : null,
  ].filter(Boolean)

  return (
    <div className="browse">
      <div className="browse-head">
        <div>
          <h1 className="browse-title">
            {typeLabel}
            {total !== null && total > 0 && <span className="browse-count"> · {total.toLocaleString()}</span>}
          </h1>
          <p className="browse-sub">{subtitleBits.join(' · ')}</p>
        </div>
        <div className="seg browse-type" role="tablist" aria-label="Movies or TV">
          <button role="tab" aria-selected={type === 'movie'} className={type === 'movie' ? 'active' : ''} onClick={() => switchType('movie')}>
            Movies
          </button>
          <button role="tab" aria-selected={type === 'tv'} className={type === 'tv' ? 'active' : ''} onClick={() => switchType('tv')}>
            TV Shows
          </button>
        </div>
      </div>

      <div className="browse-filters">
        <div className="seg" role="tablist" aria-label="Availability">
          {AVAILABILITY.map((a) => (
            <button key={a.id} role="tab" aria-selected={avail === a.id} className={avail === a.id ? 'active' : ''} onClick={() => update({ avail: a.id === 'all' ? null : a.id })}>
              {a.label}
            </button>
          ))}
        </div>
        <label className="browse-drop">
          <span>{list === 'coming-soon' ? 'Coming soon' : SORTS.find((s) => s.id === sort)?.label}</span>
          <Icon name="next" />
          <select
            aria-label="Sort"
            value={list === 'coming-soon' ? 'coming-soon' : sort}
            onChange={(e) => {
              const v = e.target.value
              if (v === 'coming-soon') update({ list: 'coming-soon', sort: null })
              else update({ list: null, sort: v === 'popular' ? null : v })
            }}
          >
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
            <option value="coming-soon">Coming soon</option>
          </select>
        </label>
        {filtersApply && (
          <>
            <label className="browse-drop">
              <span>{year ? year : 'Any year'}</span>
              <Icon name="next" />
              <select aria-label="Year" value={year ?? ''} onChange={(e) => update({ year: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Any year</option>
                {YEARS.map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </select>
            </label>
            <label className={`browse-drop${provider ? '' : ' mute'}`}>
              <span>{activeProviderName ?? 'Any service'}</span>
              <Icon name="next" />
              <select aria-label="Streaming service" value={provider ?? ''} onChange={(e) => update({ provider: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Any service</option>
                {CURATED_PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
      </div>

      {filtersApply && (
        <div className="browse-genres">
          {genres.map((g) => (
            <button key={g.id} className={g.id === genre ? 'on' : ''} onClick={() => update({ genre: g.id === genre ? null : g.id })}>
              {g.name}
            </button>
          ))}
        </div>
      )}

      <InfiniteGrid
        key={gridKey}
        queryKey={queryKey}
        fetchPage={(page) => browsePage(params, page)}
        mediaType={type}
        filterItem={filterItem}
        emptyMessage={avail === 'all' ? 'Nothing here yet.' : 'Nothing matches that filter.'}
        onTotal={onTotal}
      />
    </div>
  )
}
