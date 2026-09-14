import { Link } from 'react-router-dom'
import ClampedText from '../../components/ClampedText'
import { originalServiceMatch } from '../../lib/providers'
import type { CastMember, CrewMember, Genre } from '../../types/movies'

// Left column's first card — title/genre tags/overview(+"More" expand).
// The overview repeats the hero's own copy — deliberate, Prime-Video-
// style duplication (matches its own layout).
export function DetailOverviewPanel({
  title,
  genres,
  overview,
}: {
  title: string
  genres?: Genre[]
  overview?: string | null
}) {
  return (
    <>
      <h2 className="detail-panel-title">{title}</h2>
      {genres && genres.length > 0 && (
        <div className="detail-panel-tags">
          {genres.map((g) => (
            <span key={g.id} className="detail-panel-tag">
              {g.name}
            </span>
          ))}
        </div>
      )}
      <ClampedText text={overview ?? ''} textClassName="detail-panel-overview-text" buttonClassName="detail-panel-more-btn" />
    </>
  )
}

// Left column's second card — Directors/Producers/Cast/Studio, each a
// labeled row of comma-separated person links.
export function CreatorsAndCastPanel({
  crew,
  cast,
  studio,
}: {
  crew?: CrewMember[]
  cast?: CastMember[]
  studio?: string
}) {
  const directors = (crew || []).filter((c) => c.job === 'Director')
  const producers = (crew || []).filter((c) => c.job === 'Producer' || c.job === 'Executive Producer')
  const castTop = (cast || []).slice(0, 6)
  const rows: [string, { id: number; name: string }[]][] = []
  if (directors.length) rows.push(['Directors', directors])
  if (producers.length) rows.push(['Producers', producers])
  if (castTop.length) rows.push(['Cast', castTop])
  return (
    <>
      <h2 className="section-heading">Creators and Cast</h2>
      {rows.map(([label, people]) => (
        <div className="creators-row" key={label}>
          <div className="label">{label}</div>
          <div className="value">
            {people.map((p, i) => (
              <span key={p.id}>
                {i > 0 && ', '}
                <Link to={`/person/${p.id}`}>{p.name}</Link>
              </span>
            ))}
          </div>
        </div>
      ))}
      {studio && (
        <div className="creators-row">
          <div className="label">Studio</div>
          <div className="value">{studio}</div>
        </div>
      )}
    </>
  )
}

// Right column's bottom card — real fields only, no fabricated
// content-advisory/subtitle data.
export function DetailsCardPanel({ rows }: { rows: [string, string][] }) {
  if (!rows.length) return null
  return (
    <>
      <h2 className="section-heading">Details</h2>
      <div className="details-list">
        {rows.map(([label, value]) => (
          <div className="details-row" key={label}>
            <span className="label">{label}</span>
            <span className="value">{value}</span>
          </div>
        ))}
      </div>
    </>
  )
}

// Matches the Prime screenshot's own "Cast: Name, Name, Name" line, in
// the hero's own info column — the full photo-card row (CastRow) still
// has the complete list further down the page.
export function CastLine({ cast }: { cast?: CastMember[] }) {
  const top = (cast || []).slice(0, 4)
  if (!top.length) return null
  return (
    <div className="detail-cast-line">
      Cast:{' '}
      {top.map((c, i) => (
        <span key={c.id}>
          {i > 0 && ', '}
          <Link to={`/person/${c.id}`}>{c.name}</Link>
        </span>
      ))}
    </div>
  )
}

interface OriginalServiceSource {
  networks?: { name: string }[]
  production_companies?: { name: string }[]
  'watch/providers'?: { results?: { US?: { flatrate?: { provider_name: string }[] } } }
}

// Sits left of the score ring — Netflix/Prime/Apple TV+/etc. app icon
// when this title's own network/studio credit (or, for a movie, its US
// flatrate streaming availability) matches a known original-content
// service. Omitted entirely when there's no match.
export function DetailProviderIcon({ item, isTv }: { item: OriginalServiceSource; isTv: boolean }) {
  const match = originalServiceMatch(item, isTv)
  if (!match) return null
  return (
    <div className="detail-provider-icon" title={match.label}>
      <img src={`/icons/${match.icon}`} alt={match.label} />
    </div>
  )
}
