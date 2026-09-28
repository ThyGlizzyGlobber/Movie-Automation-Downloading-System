import { useEffect, useRef } from 'react'
import { recordTitleView } from '../../api/recommendations'

// Tells the backend someone opened this title's page — the lightest of
// the three signals their recommendations are built from (Plex watch
// history, requests, and this). Once per title per visit to its page, not
// per render or refetch, and fire-and-forget: it must never hold up or
// break the page it's on.
export function useRecordView(mediaType: 'movie' | 'tv', tmdbId: number, title: string | null | undefined) {
  const sent = useRef<string | null>(null)
  useEffect(() => {
    if (!title || !tmdbId) return
    const key = `${mediaType}:${tmdbId}`
    if (sent.current === key) return
    sent.current = key
    recordTitleView(mediaType, tmdbId, title).catch(() => {})
  }, [mediaType, tmdbId, title])
}
