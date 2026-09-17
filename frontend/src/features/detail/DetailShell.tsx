import type { ReactNode } from 'react'
import AmbientGlow from '../../components/AmbientGlow'
import Icon from '../../components/Icon'
import { backdropUrl, logoUrl, posterUrl } from '../../lib/tmdbImage'
import type { BannerPill } from '../../lib/homeHero'
import './DetailShell.css'

export interface DetailTile {
  label: string
  value: ReactNode
  tone?: 'mint' | 'ice' | 'dim'
  /* Spans the full width of the tile grid. */
  wide?: boolean
  /* In the side column, only as wide as its content; the tile beside it
     takes the rest. */
  fit?: boolean
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
// the poster glow, the poster and fact tiles on the left, and a glass
// card on the right holding the title logo (or text), meta
// pills, the synopsis, the actions and whatever the page adds beneath
// (cast, trailer). On wide desktops the poster gives way to a landscape
// banner spanning the frame with the logo and status pill inside it, and
// both columns sit underneath; narrower screens keep the poster and
// two-column layout, and on phones the card is dropped and the page
// itself is the surface.
export default function DetailShell({
  backdropPath,
  posterPath,
  logoPath,
  title,
  onPlex,
  pill,
  pills,
  overview,
  actions,
  tiles,
  details = [],
  aside,
  children,
}: {
  backdropPath: string | null | undefined
  posterPath: string | null | undefined
  logoPath: string | null | undefined
  title: string
  onPlex: boolean
  /* The pill under the logo: Watch now on Plex, Just dropped, Coming soon. */
  pill?: BannerPill | null
  pills: DetailPill[]
  overview: string | null | undefined
  actions: ReactNode
  tiles: DetailTile[]
  /* Key / value facts under the tiles (rated, language, network…). */
  details?: DetailRow[]
  /* Sits beside the synopsis and actions on wide desktops (the cast),
     and between them and the children everywhere else. */
  aside?: ReactNode
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
        {backdrop && (
          <div className="detail-banner">
            <img className="detail-banner-img" src={backdropUrl(backdropPath, 'original')} alt="" />
            <div className="detail-banner-fade" />
            <div className="detail-banner-text">
              <div className={`detail-title${logo ? ' has-logo' : ''}`}>{logo ? <img className="detail-logo" src={logoUrl(logoPath, 'original') ?? logo} alt={title} /> : title}</div>
              {pill && <span className={`on-plex-badge detail-banner-plex ${pill.tone}`}>{pill.text}</span>}
            </div>
          </div>
        )}
        <div className="detail-grid">
          <aside className="detail-side">
            <div className="detail-poster-row">
              <div className="detail-poster-wrap">
                <img className="detail-poster" src={posterUrl(posterPath)} alt="" />
                {onPlex && <span className="on-plex-badge">On Plex</span>}
              </div>
              {tiles.length > 0 && (
                <div className="detail-tiles">
                  {tiles.map((t) => (
                    <div className={`detail-tile${t.tone ? ` ${t.tone}` : ''}${t.wide ? ' wide' : ''}${t.fit ? ' fit' : ''}`} key={t.label}>
                      <small>{t.label}</small>
                      <b>{t.value}</b>
                    </div>
                  ))}
                </div>
              )}
            </div>
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
          </aside>
          <section className="detail-card">
            <h1 className={`detail-title${logo ? ' has-logo' : ''}`}>{logo ? <img className="detail-logo" src={logo} alt={title} /> : title}</h1>
            <div className="detail-card-main">
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
            </div>
            {aside && <div className="detail-card-aside">{aside}</div>}
            <div className="detail-card-more">{children}</div>
          </section>
        </div>
      </div>
    </div>
  )
}

export function DetailH4({ children }: { children: ReactNode }) {
  return <h4 className="detail-h4">{children}</h4>
}
