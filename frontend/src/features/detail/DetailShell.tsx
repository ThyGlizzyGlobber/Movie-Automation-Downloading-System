import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import AmbientGlow from '../../components/AmbientGlow'
import Icon from '../../components/Icon'
import { backdropUrl, logoUrl, posterUrl } from '../../lib/tmdbImage'
import { relativeTime } from '../../lib/format'
import StatusPill from '../../components/StatusPill'
import type { RequestOut } from '../../types/requests'
import './DetailShell.css'

export interface DetailTile {
  label: string
  value: ReactNode
  tone?: 'mint' | 'ice' | 'dim'
  /* Spans the full width of the tile grid. */
  wide?: boolean
}

export interface DetailRow {
  label: string
  value: ReactNode
}

export interface DetailPill {
  text: ReactNode
  mute?: boolean
  star?: boolean
}

// The content page frame from the reference: a blurred backdrop with
// the poster glow, a back link, the poster and four fact tiles on the
// left, and a glass card on the right holding the eyebrow, the title
// logo (or text), meta pills, the synopsis, the actions and whatever
// the page adds beneath (cast, tiles, episodes). On phones the card is
// dropped and the page itself is the surface.
export default function DetailShell({
  backTo,
  backLabel,
  backdropPath,
  posterPath,
  logoPath,
  title,
  onPlex,
  eyebrow,
  eyebrowTone = 'ice',
  pills,
  overview,
  actions,
  tiles,
  details = [],
  requests = [],
  requestLabel,
  children,
}: {
  backTo: string
  backLabel: string
  backdropPath: string | null | undefined
  posterPath: string | null | undefined
  logoPath: string | null | undefined
  title: string
  onPlex: boolean
  /* Status line above the title; omitted when empty. */
  eyebrow?: string
  eyebrowTone?: 'ice' | 'mint' | 'dim' | 'amber'
  pills: DetailPill[]
  overview: string | null | undefined
  actions: ReactNode
  tiles: DetailTile[]
  /* Key / value facts under the tiles (rated, language, network…). */
  details?: DetailRow[]
  /* The household's requests for this title, newest first. */
  requests?: RequestOut[]
  requestLabel?: (r: RequestOut) => string
  children?: ReactNode
}) {
  const logo = logoUrl(logoPath)
  const backdrop = backdropUrl(backdropPath)
  return (
    <div className="detail">
      <AmbientGlow posterPath={posterPath} />
      {backdrop && <img className="detail-bd" src={backdrop} alt="" />}
      <div className="detail-fade" />
      <div className="detail-in">
        <Link className="detail-back" to={backTo}>
          <span className="detail-circ">
            <Icon name="back" />
          </span>
          {backLabel}
        </Link>
        <div className="detail-grid">
          <aside className="detail-side">
            <div className="detail-poster-wrap">
              <img className="detail-poster" src={posterUrl(posterPath)} alt="" />
              {onPlex && <span className="on-plex-badge">On Plex</span>}
            </div>
            {tiles.length > 0 && (
              <div className="detail-tiles">
                {tiles.map((t) => (
                  <div className={`detail-tile${t.tone ? ` ${t.tone}` : ''}${t.wide ? ' wide' : ''}`} key={t.label}>
                    <small>{t.label}</small>
                    <b>{t.value}</b>
                  </div>
                ))}
              </div>
            )}
            {details.length > 0 && (
              <dl className="detail-facts">
                {details.map((d) => (
                  <div className="detail-fact" key={d.label}>
                    <dt>{d.label}</dt>
                    <dd>{d.value}</dd>
                  </div>
                ))}
              </dl>
            )}
            {requests.length > 0 && (
              <div className="detail-requests">
                <small>In the household</small>
                {requests.slice(0, 5).map((r) => (
                  <a className="detail-request" key={r.id} href="#/requests" title={r.error_message || undefined}>
                    <span className="detail-request-text">
                      <b>{requestLabel ? requestLabel(r) : r.requested_by_username || 'Someone'}</b>
                      <i>
                        {requestLabel && r.requested_by_username ? `${r.requested_by_username} · ` : ''}
                        {r.status === 'no qualifying results' && r.error_message ? r.error_message : relativeTime(r.updated_at)}
                      </i>
                    </span>
                    <StatusPill status={r.status} />
                  </a>
                ))}
              </div>
            )}
          </aside>
          <section className="detail-card">
            {eyebrow && (
              <span className={`detail-eyebrow ${eyebrowTone}`}>
                <b />
                {eyebrow}
              </span>
            )}
            <h1 className={`detail-title${logo ? ' has-logo' : ''}`}>{logo ? <img className="detail-logo" src={logo} alt={title} /> : title}</h1>
            <div className="detail-meta">
              {pills.map((p, i) => (
                <span key={i} className={`detail-pill${p.mute ? ' mute' : ''}`}>
                  {p.star && <Icon name="star" className="detail-star" />}
                  {p.text}
                </span>
              ))}
            </div>
            {overview && <p className="detail-syn">{overview}</p>}
            <div className="detail-actions">{actions}</div>
            {children}
          </section>
        </div>
      </div>
    </div>
  )
}

export function DetailH4({ children }: { children: ReactNode }) {
  return <h4 className="detail-h4">{children}</h4>
}
