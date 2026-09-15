// Rank movement for the Top 10 row. TMDB's trending endpoint gives the
// current order only, so the previous order is remembered locally: one
// snapshot per media type, replaced once it is at least a week old. A
// title's chip compares its rank now with its rank in that snapshot —
// "New" if it wasn't in it, otherwise the number of places moved. The
// first ever visit has no snapshot, so no chips; they appear from the
// next week on. Local-only and best effort: storage can be missing or
// disabled, in which case there are simply no chips.
export type RankMove = { kind: 'new' } | { kind: 'up' | 'down'; by: number } | { kind: 'same' }

const SNAPSHOT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

interface Snapshot {
  ids: number[]
  savedAt: number
}

function key(mediaType: 'movie' | 'tv'): string {
  return `meridian.top10.${mediaType}`
}

function readSnapshot(mediaType: 'movie' | 'tv'): Snapshot | null {
  try {
    const raw = localStorage.getItem(key(mediaType))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Snapshot
    if (!Array.isArray(parsed.ids) || typeof parsed.savedAt !== 'number') return null
    return parsed
  } catch {
    return null
  }
}

function writeSnapshot(mediaType: 'movie' | 'tv', ids: number[]) {
  try {
    localStorage.setItem(key(mediaType), JSON.stringify({ ids, savedAt: Date.now() } satisfies Snapshot))
  } catch {
    /* storage unavailable — chips just stay off */
  }
}

// Returns one RankMove per id, in order, and rolls the snapshot forward
// when the stored one is a week old (or missing).
export function rankMoves(mediaType: 'movie' | 'tv', ids: number[]): RankMove[] {
  const snapshot = readSnapshot(mediaType)
  const stale = !snapshot || Date.now() - snapshot.savedAt >= SNAPSHOT_MAX_AGE_MS
  const previous = snapshot?.ids ?? []
  const moves: RankMove[] = ids.map((id, index) => {
    if (!snapshot) return { kind: 'same' }
    const was = previous.indexOf(id)
    if (was === -1) return { kind: 'new' }
    if (was === index) return { kind: 'same' }
    return was > index ? { kind: 'up', by: was - index } : { kind: 'down', by: index - was }
  })
  if (stale && ids.length) writeSnapshot(mediaType, ids)
  return moves
}
