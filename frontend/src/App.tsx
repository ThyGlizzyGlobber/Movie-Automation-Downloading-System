// Temporary smoke-test harness for Part A5's API client — calls the real
// backend (via Vite's dev proxy) and dumps raw responses so the hand-written
// types can be checked against what actually comes back. Replaced by real
// routing/pages in a later step.
import { useEffect, useState } from 'react'
import { getHealth, getStorage } from './api/system'
import { getDiscoverPopular } from './api/movies'
import { getTvDiscoverPopular } from './api/tv'
import { listRequests } from './api/requests'
import { getPipelineSettings, getTvSettings } from './api/settings'
import { getPlexStatus } from './api/plex'

function Probe({ name, fn }: { name: string; fn: () => Promise<unknown> }) {
  const [state, setState] = useState<{ ok: boolean; data: unknown } | null>(null)

  useEffect(() => {
    fn()
      .then((data) => setState({ ok: true, data }))
      .catch((err) => setState({ ok: false, data: String(err) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <section style={{ marginBottom: 24 }}>
      <h3 style={{ fontFamily: 'var(--font-display)' }}>
        {name} {state && (state.ok ? '✅' : '❌')}
      </h3>
      <pre style={{ background: 'var(--bg-card)', padding: 12, borderRadius: 8, overflow: 'auto', maxHeight: 300 }}>
        {state ? JSON.stringify(state.data, null, 2) : 'loading…'}
      </pre>
    </section>
  )
}

function App() {
  return (
    <div style={{ padding: 40 }}>
      <Probe name="getHealth" fn={getHealth} />
      <Probe name="getStorage" fn={getStorage} />
      <Probe name="getDiscoverPopular" fn={() => getDiscoverPopular(1)} />
      <Probe name="getTvDiscoverPopular" fn={() => getTvDiscoverPopular(1)} />
      <Probe name="listRequests" fn={() => listRequests()} />
      <Probe name="getPipelineSettings" fn={getPipelineSettings} />
      <Probe name="getTvSettings" fn={getTvSettings} />
      <Probe name="getPlexStatus" fn={getPlexStatus} />
    </div>
  )
}

export default App
