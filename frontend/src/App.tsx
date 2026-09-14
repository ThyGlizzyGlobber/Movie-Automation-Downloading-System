// Temporary visual-verification harness for Part A2's primitives — sample
// data only, no API calls. Replaced by real routing/pages in a later step.
import StatusPill from './components/StatusPill'
import LoadingState from './components/LoadingState'
import ErrorState from './components/ErrorState'
import EmptyState from './components/EmptyState'
import PosterCard from './components/PosterCard'
import ProviderChips from './components/ProviderChips'

const SAMPLE_STATUSES = [
  'queued',
  'searching',
  'downloading',
  'complete',
  'no qualifying results',
  'insufficient free space',
  'failed',
  'cancelled',
]

const SAMPLE_POSTERS = [
  { id: 1, title: 'Sample Movie One', poster_path: null, on_plex: true },
  { id: 2, title: 'Sample Movie Two, With A Longer Title That Wraps', poster_path: null, on_plex: false },
]

function App() {
  return (
    <div style={{ padding: 40, display: 'flex', flexDirection: 'column', gap: 32 }}>
      <section>
        <h2 style={{ fontFamily: 'var(--font-display)' }}>StatusPill</h2>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {SAMPLE_STATUSES.map((s) => (
            <StatusPill key={s} status={s} />
          ))}
        </div>
      </section>

      <section>
        <h2 style={{ fontFamily: 'var(--font-display)' }}>PosterCard</h2>
        <div style={{ display: 'flex', gap: 16, width: 300 }}>
          {SAMPLE_POSTERS.map((item) => (
            <PosterCard key={item.id} item={item} mediaType="movie" />
          ))}
        </div>
      </section>

      <section>
        <h2 style={{ fontFamily: 'var(--font-display)' }}>ProviderChips</h2>
        <ProviderChips hrefPrefix="#/movies/provider" />
      </section>

      <section>
        <h2 style={{ fontFamily: 'var(--font-display)' }}>LoadingState</h2>
        <LoadingState />
      </section>

      <section>
        <h2 style={{ fontFamily: 'var(--font-display)' }}>ErrorState</h2>
        <ErrorState message="Sample error message" retryHref="#/home" />
      </section>

      <section>
        <h2 style={{ fontFamily: 'var(--font-display)' }}>EmptyState</h2>
        <EmptyState message="Nothing here yet." />
      </section>
    </div>
  )
}

export default App
