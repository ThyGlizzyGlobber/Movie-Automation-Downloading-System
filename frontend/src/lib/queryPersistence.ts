import type { Query, QueryClient } from '@tanstack/react-query'
import { removeOldestQuery, type PersistQueryClientOptions } from '@tanstack/react-query-persist-client'
import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister'

// The landing pages' feeds, kept in this browser between visits, the way
// a streaming app opens on what it showed last time: a cold start (which
// iOS gives a home-screen app often) or a return after a while draws
// Home's hero and rows straight away and refreshes them behind, instead
// of skeletons for as long as the round trips take. Only these: they are
// what the first screen is made of, and a few hundred KB between them.
// Title details stay in memory only — there are too many, and each one
// is large.
const STORAGE_KEY = 'obsidian-query-cache'
const MAX_AGE_MS = 24 * 60 * 60_000
const PERSISTED: readonly (readonly string[])[] = [
  ['hero'],
  ['recommendations'],
  ['movies', 'trending'],
  ['tv', 'trending'],
  ['shows'],
  ['requests'],
  ['plex-on-deck'],
  ['plex-recently-added'],
]

function isPersisted(key: readonly unknown[]): boolean {
  return PERSISTED.some((prefix) => prefix.every((part, i) => key[i] === part))
}

function storage(): Storage | undefined {
  // Can throw outright (blocked site data, some private modes); the app
  // then simply runs without it.
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

export function persistOptions(queryClient: QueryClient): Omit<PersistQueryClientOptions, 'queryClient'> {
  // A query nothing is showing is dropped from memory after gcTime (five
  // minutes by default), and from storage with it. These have to outlive
  // the visit to be there for the next one.
  for (const prefix of PERSISTED) queryClient.setQueryDefaults(prefix, { gcTime: MAX_AGE_MS })
  return {
    persister: createSyncStoragePersister({ storage: storage(), key: STORAGE_KEY, throttleTime: 3000, retry: removeOldestQuery }),
    maxAge: MAX_AGE_MS,
    // A new build may reshape what the API sends; never hand it data
    // saved by an older one.
    buster: __BUILD_ID__,
    dehydrateOptions: { shouldDehydrateQuery: (query: Query) => query.state.status === 'success' && isPersisted(query.queryKey) },
  }
}

// On sign-out: the rows are dealt per person, and the next person to sign
// in on this device shouldn't open on someone else's. Out of memory as
// well as storage, or the next save would simply write them back.
export function forgetPersistedQueries(queryClient: QueryClient) {
  queryClient.removeQueries({ predicate: (query) => isPersisted(query.queryKey) })
  try {
    storage()?.removeItem(STORAGE_KEY)
  } catch {
    // nothing to forget
  }
}
