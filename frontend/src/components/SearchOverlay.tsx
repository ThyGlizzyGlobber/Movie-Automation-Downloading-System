import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import Icon from './Icon'
import SearchResults from './SearchResults'
import SearchSuggestions from './SearchSuggestions'
import { readRecentSearches, rememberSearch } from '../lib/recentSearches'
import { hitHref, useLiveSearch, type SearchHit } from '../lib/liveSearch'
import './SearchOverlay.css'

// The search palette: a glass panel with the query field, live results
// with inline request actions (SearchResults), and, while the field is
// empty, recent searches plus what's trending and titles like the
// household's latest request (SearchSuggestions). Enter opens the highlighted result, or runs the
// full search page when nothing is highlighted.
export default function SearchOverlay({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [value, setValue] = useState('')
  const [recent, setRecent] = useState<string[]>([])
  const [highlighted, setHighlighted] = useState(-1)
  const [tab, setTab] = useState<'all' | 'movie' | 'tv'>('all')
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const live = useLiveSearch(value)
  const typed = value.trim()
  const allHits: SearchHit[] = typed.length >= 2 ? (live.data ?? []) : []
  const hits = tab === 'all' ? allHits : allHits.filter((h) => h.mediaType === tab)

  useEffect(() => {
    document.body.classList.toggle('search-open', isOpen)
    if (isOpen) {
      inputRef.current?.focus()
      setRecent(readRecentSearches())
    } else {
      // Closing (Escape, backdrop tap) starts the next search fresh
      // rather than reopening on the previous text.
      setValue('')
      setHighlighted(-1)
      setTab('all')
    }
  }, [isOpen])

  // A new set of results resets the keyboard highlight.
  useEffect(() => {
    setHighlighted(-1)
  }, [live.data])

  useEffect(() => {
    function onKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape' && isOpen) onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [isOpen, onClose])

  function finish() {
    setValue('')
    onClose()
  }

  function submit(query = value) {
    const q = query.trim()
    if (!q) return
    rememberSearch(q)
    navigate(`/search/${encodeURIComponent(q)}`)
    finish()
  }

  function openHit(hit: SearchHit) {
    rememberSearch(typed)
    navigate(hitHref(hit))
    finish()
  }

  function onInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown' && hits.length) {
      e.preventDefault()
      setHighlighted((h) => (h + 1) % hits.length)
    } else if (e.key === 'ArrowUp' && hits.length) {
      e.preventDefault()
      setHighlighted((h) => (h <= 0 ? hits.length - 1 : h - 1))
    } else if (e.key === 'Enter') {
      if (highlighted >= 0 && hits[highlighted]) openHit(hits[highlighted])
      else submit()
    }
  }

  if (!isOpen) return null

  return (
    <div
      id="searchOverlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="search-overlay-panel">
        <div className="search-overlay-pill">
          <button className="search-overlay-icon-btn" aria-label="Search" onClick={() => submit()}>
            <Icon name="search" />
          </button>
          <input
            ref={inputRef}
            type="search"
            enterKeyHint="search"
            aria-label="Search movies and TV shows"
            value={value}
            placeholder="Search movies and TV shows"
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onInputKeyDown}
          />
          {value && (
            <button
              className="search-clear-btn"
              aria-label="Clear search"
              onClick={() => {
                setValue('')
                inputRef.current?.focus()
              }}
            >
              ✕
            </button>
          )}
          <kbd className="search-overlay-kbd">esc</kbd>
        </div>
        {typed.length >= 2 && allHits.length > 0 && (
          <div className="search-overlay-tabs">
            <div className="seg" role="tablist" aria-label="Result type">
              {(
                [
                  ['all', 'All'],
                  ['movie', 'Movies'],
                  ['tv', 'TV Shows'],
                ] as const
              ).map(([id, label]) => (
                <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
                  {label}
                </button>
              ))}
            </div>
            <span className="search-overlay-k">
              {hits.length} result{hits.length === 1 ? '' : 's'} · ↑↓ to move · ↵ to open
            </span>
          </div>
        )}
        {hits.length > 0 && (
          <>
            <SearchResults hits={hits} highlighted={highlighted} onOpen={openHit} onHover={setHighlighted} />
            <button className="search-overlay-all" onClick={() => submit()}>
              All results for “{typed}”
              <Icon name="arrow" />
            </button>
          </>
        )}
        {typed.length >= 2 && live.isFetched && allHits.length === 0 && (
          <div className="search-overlay-none">Nothing matched “{typed}”. Press Enter to search anyway.</div>
        )}
        {!typed && recent.length > 0 && (
          <div className="search-overlay-recent">
            <span className="search-overlay-recent-label">Recent</span>
            {recent.map((q) => (
              <button key={q} className="search-overlay-recent-chip" onClick={() => submit(q)}>
                {q}
              </button>
            ))}
          </div>
        )}
        {!typed && <SearchSuggestions onPick={finish} />}
      </div>
    </div>
  )
}
