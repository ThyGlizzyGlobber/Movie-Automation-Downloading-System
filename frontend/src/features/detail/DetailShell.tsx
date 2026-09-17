import type { ReactNode } from 'react'
import AmbientGlow from '../../components/AmbientGlow'
import Icon from '../../components/Icon'
import Img from '../../components/Img'
import { Skel, SkelText, SkelWords } from '../../components/Skeleton'
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
//
// DetailFrame is the markup; DetailShell fills it with a title and
// DetailShellSkeleton with loading placeholders, so the two always share
// one layout at every width.
function DetailFrame({
  glowPath,
  backdrop,
  banner,
  poster,
  tiles,
  facts,
  title,
  meta,
  overview,
  actions,
  aside,
  children,
  busy = false,
}: {
  glowPath: string | null | undefined
  backdrop: ReactNode
  banner: ReactNode
  poster: ReactNode
  tiles: ReactNode
  facts: ReactNode
  title: ReactNode
  meta: ReactNode
  overview: ReactNode
  actions: ReactNode
  aside?: ReactNode
  children?: ReactNode
  busy?: boolean
}) {
  return (
    <div className="detail" aria-busy={busy || undefined}>
      <AmbientGlow posterPath={glowPath} />
      {backdrop}
      <div className="detail-fade" />
      <div className="detail-in">
        {banner}
        <div className="detail-grid">
          <aside className="detail-side">
            <div className="detail-poster-row">
              <div className="detail-poster-wrap">{poster}</div>
              {tiles}
            </div>
            {facts}
          </aside>
          <section className="detail-card">
            {title}
            <div className="detail-card-main">
              <div className="detail-meta">{meta}</div>
              {overview}
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

function tileClass(t: Pick<DetailTile, 'tone' | 'wide' | 'fit'>) {
  return `detail-tile${t.tone ? ` ${t.tone}` : ''}${t.wide ? ' wide' : ''}${t.fit ? ' fit' : ''}`
}

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
    <DetailFrame
      glowPath={posterPath}
      backdrop={backdrop && <Img className="detail-bd" src={backdrop} alt="" plain />}
      banner={
        backdrop && (
          <div className="detail-banner">
            <Img className="detail-banner-img" src={backdropUrl(backdropPath, 'original')} alt="" />
            <div className="detail-banner-fade" />
            <div className="detail-banner-text">
              <div className={`detail-title${logo ? ' has-logo' : ''}`}>{logo ? <Img className="detail-logo" src={logoUrl(logoPath, 'original') ?? logo} alt={title} plain /> : title}</div>
              {pill && <span className={`on-plex-badge detail-banner-plex ${pill.tone}`}>{pill.text}</span>}
            </div>
          </div>
        )
      }
      poster={
        <>
          <Img className="detail-poster" src={posterUrl(posterPath)} alt="" />
          {onPlex && <span className="on-plex-badge">On Plex</span>}
        </>
      }
      tiles={
        tiles.length > 0 && (
          <div className="detail-tiles">
            {tiles.map((t) => (
              <div className={tileClass(t)} key={t.label}>
                <small>{t.label}</small>
                <b>{t.value}</b>
              </div>
            ))}
          </div>
        )
      }
      facts={
        details.length > 0 && (
          <dl className="detail-facts">
            {details.map((d) => (
              <div className="detail-fact" key={d.label}>
                <dt>{d.label}</dt>
                <dd>{d.value}</dd>
              </div>
            ))}
          </dl>
        )
      }
      title={<h1 className={`detail-title${logo ? ' has-logo' : ''}`}>{logo ? <Img className="detail-logo" src={logo} alt={title} plain /> : title}</h1>}
      meta={pills.map((p, i) => (
        <span key={i} className={`detail-pill${p.mute ? ' mute' : ''}`}>
          {p.star && <Icon name="star" className="detail-star" />}
          {p.text}
        </span>
      ))}
      overview={overview && <p className="detail-syn">{overview}</p>}
      actions={actions}
      aside={aside}
    >
      {children}
    </DetailFrame>
  )
}

/* The shapes a page's loading state draws: its usual side tiles, how
   many facts and pills it tends to have, and its action buttons. */
export interface DetailSkeletonShape {
  tiles: Pick<DetailTile, 'wide' | 'fit'>[]
  facts: number
  /* Sample text for the pills before the genres line, so each is as
     wide as a real one. */
  pills: string[]
  /* Sample labels for the action buttons, the primary first. */
  buttons: string[]
}

const FACT_WIDTHS = ['38%', '62%', '44%', '70%', '56%', '80%', '48%', '66%']
// A synopsis of typical length (about 300 characters) and a genres line.
const SAMPLE_SYNOPSIS =
  'A reluctant hero is pulled back into a world they left behind when an old friend goes missing, and every step toward the truth costs something. Old loyalties are tested, a city keeps its secrets, and the only way out is through the people they swore never to trust again before it is too late.'
const SAMPLE_GENRES = 'Science Fiction · Adventure'

export function DetailShellSkeleton({ shape, aside, children }: { shape: DetailSkeletonShape; aside?: ReactNode; children?: ReactNode }) {
  return (
    <DetailFrame
      busy
      glowPath={null}
      backdrop={null}
      banner={
        <div className="detail-banner skel" aria-hidden="true">
          <div className="detail-banner-text">
            <div className="detail-title has-logo detail-title-skel">
              <Skel className="detail-logo detail-logo-skel" />
            </div>
            <Skel className="on-plex-badge detail-banner-plex detail-badge-skel">&nbsp;</Skel>
          </div>
        </div>
      }
      poster={<Skel className="detail-poster" />}
      tiles={
        <div className="detail-tiles" aria-hidden="true">
          {shape.tiles.map((t, i) => (
            <div className={tileClass(t)} key={i}>
              <small>
                <SkelText width="46%" />
              </small>
              <b>
                <SkelText width={t.fit ? '6.5em' : '72%'} />
              </b>
            </div>
          ))}
        </div>
      }
      facts={
        <dl className="detail-facts" aria-hidden="true">
          {Array.from({ length: shape.facts }, (_, i) => (
            <div className="detail-fact" key={i}>
              <dt>
                <SkelText width="70%" />
              </dt>
              <dd>
                <SkelText width={FACT_WIDTHS[i % FACT_WIDTHS.length]} />
              </dd>
            </div>
          ))}
        </dl>
      }
      title={null}
      meta={
        <>
          {shape.pills.map((text, i) => (
            <Skel key={i} className="detail-pill">
              {i === 0 && <Icon name="star" className="detail-star" />}
              {text}
            </Skel>
          ))}
          <span className="detail-pill mute" aria-hidden="true">
            <SkelWords text={SAMPLE_GENRES} />
          </span>
        </>
      }
      overview={
        <p className="detail-syn" aria-hidden="true">
          <SkelWords text={SAMPLE_SYNOPSIS} />
        </p>
      }
      actions={shape.buttons.map((label, i) => (
        <Skel key={i} className={`btn ${i === 0 ? 'pri' : 'sec'}`}>
          <Icon name="plus" />
          {label}
        </Skel>
      ))}
      aside={aside}
    >
      {children}
    </DetailFrame>
  )
}

export function DetailH4({ children }: { children: ReactNode }) {
  return <h4 className="detail-h4">{children}</h4>
}
