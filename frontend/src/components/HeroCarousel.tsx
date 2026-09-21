import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { getMovieTrailer } from '../api/movies'
import { getTvTrailer } from '../api/tv'
import { backdropUrl, logoUrl, posterUrl } from '../lib/tmdbImage'
import { yearOf } from '../lib/detailHelpers'
import { movieHeroBadge, movieCertOf, tvHeroBadge, tvCertOf } from '../lib/homeHero'
import type { HeroSlide } from '../types/hero'
import AmbientGlow from './AmbientGlow'
import './HeroCarousel.css'
import Icon from './Icon'
import { SAMPLE_SYNOPSIS, Skel, SkelText, SkelWords } from './Skeleton'
import { useMediaQuery } from '../lib/hooks'
import { useCertificationRegion } from '../features/auth/useSession'

const HERO_AUTOPLAY_MS = 7000 // flat dwell time for a poster-only slide
// How long a trailer slide's poster shows on its own — both before the
// trailer starts and again after it finishes, before actually advancing.
const HERO_POSTER_LEAD_MS = 3000
// How close to the synopsis counts as reaching for it, in px on every
// side. Generous enough that it opens before the cursor is literally on
// the text — which matters while it is hidden and there is nothing to
// aim at but the gap where it goes.
const CURSOR_NEAR_PAD_PX = 96

// The primary button's label rolls from what the title *is* ("On Plex")
// to what tapping it *does* ("Watch now") a moment after the slide
// shows, the way Prime's hero settles. Re-runs whenever the slide
// becomes active again.
function FlipLabel({ first, second, active }: { first: string; second: string; active: boolean }) {
  const [flipped, setFlipped] = useState(false)
  useEffect(() => {
    setFlipped(false)
    if (!active) return
    const t = window.setTimeout(() => setFlipped(true), 1600)
    return () => window.clearTimeout(t)
  }, [active, first, second])
  return (
    <span className={`flip${flipped ? ' flipped' : ''}`} aria-label={flipped ? second : first}>
      <span aria-hidden="true">{first}</span>
      <span aria-hidden="true">{second}</span>
    </span>
  )
}

interface Enrichment {
  badge: string | null
  cert: string
  /* "2h 14m" for a movie, "2 seasons" for a show. */
  length: string
  genres: string
  /* Title logo (transparent art) when TMDB has one. */
  logo: string | null
}

function movieLength(runtime: number | null): string {
  if (!runtime) return ''
  const h = Math.floor(runtime / 60)
  const m = runtime % 60
  return h ? `${h}h ${m}m` : `${m}m`
}

const HERO_SKELETON_DOTS = 5

// The colour wash the content page's banner sits in (DetailShell.css),
// brought to the landing heroes. It can't live inside .home-hero — that
// pane clips its own overflow to keep the artwork's rounded edge, and a
// glow inside it is hidden behind the backdrop anyway — so the hero sits
// in a wrapper and this is a layer behind it, bleeding out past the pane
// on every side and washing down onto the page ground beneath it.
//
// Both of the content page's layers, in its order: the poster-tinted
// blobs, then the backdrop screen-blended over them (.detail-bd's twin,
// see .home-hero-glow-bd). Without the second one the blobs sit on pure
// black, and a title with bright key art — measured: The Scandal's
// backdrop is mean luma 142/255 against Reacher's 10 — reads far darker
// here than on its own content page, where that layer lifts the ground
// by around a fifth before the blobs contribute anything. The pane has
// already loaded this exact URL, so it costs no fetch.
function HeroGlow({ items, activeIndex }: { items: HeroSlide[]; activeIndex: number }) {
  const backdrops = items.map((item) => backdropUrl(item.backdrop_path))
  return (
    <div className="home-hero-glow" aria-hidden="true">
      <AmbientGlow posterPath={items[activeIndex]?.poster_path} />
      {backdrops.some(Boolean) && (
        /* Every slide's backdrop, stacked, so the ground can crossfade
           with the artwork instead of stepping under it. They are the
           URLs the slides themselves load, and every slide sits in the
           viewport, so the browser has all of these already. */
        <div className="home-hero-glow-bd">
          {items.map((item, i) =>
            backdrops[i] ? <img key={item.id} className={i === activeIndex ? 'active' : undefined} src={backdrops[i]} alt="" /> : null,
          )}
        </div>
      )}
    </div>
  )
}


// The hero while its titles load: one slide of the real markup, so the
// same CSS shapes it — a scope frame with the art, logo, status line,
// blurb and buttons over it on desktop, and the same elements as a
// portrait card with the facts line and two stacked buttons on a phone.
function HeroSkeleton() {
  return (
    <div className="home-hero-wrap">
      <HeroGlow items={[]} activeIndex={0} />
      <div className="home-hero" aria-busy="true">
        <div className="home-hero-slide active" aria-hidden="true">
          <AmbientGlow posterPath={null} />
          <div className="home-hero-media">
            <Skel className="home-hero-art-skel" />
          </div>
          <div className="home-hero-fade" />
          <div className="home-hero-content">
            <div className="home-hero-body">
              <h1 className="has-logo">
                <Skel className="hero-logo hero-logo-skel" />
              </h1>
              <div className="hero-line">
                <Skel className="hero-line-skel">
                  <Icon name="chart" />
                  #1 trending this week
                </Skel>
              </div>
              {/* Phone-only, like the real one: the wrapper's own rule
                  hides it above 640px, taking the bar with it. */}
              <div className="home-hero-meta">
                <SkelText width="68%" />
              </div>
              {/* skel-clamped: the clamp draws its own ellipsis, which
                  has to be hidden along with the sample text — see
                  .hero-syn.skel-clamped in the CSS. */}
              <p className="hero-syn skel-clamped">
                <SkelWords text={SAMPLE_SYNOPSIS} />
              </p>
              <div className="home-hero-actions">
                <Skel className="btn pri">
                  <Icon name="plus" />
                  Not on Plex yet
                </Skel>
                {/* Carries home-hero-info and the label, so on a phone it
                    widens into the second full-width button rather than
                    staying a 54px circle the loaded card doesn't have. */}
                <Skel className="btn sec circ home-hero-info">
                  <Icon name="info" />
                  <span className="home-hero-info-label">More info</span>
                </Skel>
              </div>
            </div>
          </div>
        </div>
        <div className="home-hero-dots" aria-hidden="true">
          {Array.from({ length: HERO_SKELETON_DOTS }, (_, i) => (
            <span key={i} className={`home-hero-dot${i === 0 ? ' active' : ''}`} />
          ))}
        </div>
      </div>
    </div>
  )
}

export default function HeroCarousel({ items, loading = false }: { items: HeroSlide[]; loading?: boolean }) {
  // Phones get a different hero entirely (see HeroCarousel.css): portrait
  // key art in a card, with the meta line, synopsis and two stacked
  // buttons below it rather than overlaid on a scope frame.
  const isPhone = useMediaQuery('(max-width: 639px)')
  const region = useCertificationRegion()
  // And no trailers there. The card's art is a portrait poster, which a
  // landscape trailer can't fill without cropping it to a strip, and it
  // would cost a mobile connection a video fetch per slide. Gates the
  // fetch too, not just playback, so nothing is downloaded to sit unused.
  const trailersEnabled = !isPhone
  const [activeIndex, setActiveIndex] = useState(0)
  const [muted, setMuted] = useState(true)
  const [videoUrls, setVideoUrls] = useState<Record<number, string | null>>({})
  const [videoVisible, setVideoVisible] = useState<Record<number, boolean>>({})
  const [scheduleTick, setScheduleTick] = useState(0)
  // While a trailer plays the blurb steps aside; it comes back when the
  // cursor is near the hero's text (Prime's behaviour).
  const [cursorNear, setCursorNear] = useState(false)
  const heroRef = useRef<HTMLDivElement>(null)

  const videoRefs = useRef<(HTMLVideoElement | null)[]>([])
  const timerRef = useRef<number | null>(null)
  const tokenRef = useRef(0)
  const stallTimersRef = useRef<Record<number, number>>({})
  const pausedByScrollRef = useRef(false)
  const prevIndexRef = useRef(0)

  // Trailer fetch for every slide — self-hosted, chromeless <video>,
  // never autoplaying on its own; playback is entirely driven by the
  // scheduling effect below, gated on the slide actually being active.
  //
  // One at a time, in the order the slides come up, rather than all at
  // once. A hit is a cached file and answers immediately, so a warm
  // hero fills in as fast either way; a miss makes the backend fetch
  // the clip from YouTube, and firing five of those in parallel puts
  // five yt-dlp downloads on the NAS at once for a carousel that shows
  // one slide every seven seconds. Sequential, the work arrives roughly
  // in the order it is needed and a cold hero simply gains its trailers
  // over the first pass or two.
  useEffect(() => {
    if (!trailersEnabled) {
      // Also clears anything fetched before the viewport narrowed, so
      // rotating a phone doesn't leave a video mid-play.
      setVideoUrls({})
      setVideoVisible({})
      return
    }
    let cancelled = false
    void (async () => {
      for (let i = 0; i < items.length; i++) {
        if (cancelled) return
        const item = items[i]
        try {
          const { url } = item.mediaType === 'tv' ? await getTvTrailer(item.id) : await getMovieTrailer(item.id)
          if (cancelled) return
          setVideoUrls((prev) => ({ ...prev, [i]: url }))
        } catch {
          // no trailer on file — the poster stays as the slide's art
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [items, trailersEnabled])

  // Badge, certification, length and genre line for each slide. These
  // used to arrive from a detail call per slide, fired here — which put
  // two sequential round trips in front of the title logo, because its
  // path only came back with that call and the <img> could not exist
  // until it did. /api/hero now carries those fields with the slides
  // themselves, so this is derivation rather than fetching, and the
  // logo starts loading the moment the carousel has anything to show.
  const enrichment = useMemo<Record<number, Enrichment>>(() => {
    const out: Record<number, Enrichment> = {}
    items.forEach((item, i) => {
      const genres = (item.genres ?? []).slice(0, 2).map((g) => g.name).join(' · ')
      const logo = logoUrl(item.logo_path)
      if (item.mediaType === 'tv') {
        const seasons = (item.seasons ?? []).filter((se) => se.season_number > 0).length
        out[i] = {
          badge: tvHeroBadge(item),
          cert: tvCertOf(item, region),
          length: seasons ? `${seasons} season${seasons === 1 ? '' : 's'}` : '',
          genres,
          logo,
        }
      } else {
        out[i] = {
          badge: movieHeroBadge(item, region),
          cert: movieCertOf(item, region),
          length: movieLength(item.runtime ?? null),
          genres,
          logo,
        }
      }
    })
    return out
  }, [items, region])

  // Reset mute to "on" every time the active slide changes — never
  // carried over between titles.
  useEffect(() => {
    setMuted(true)
  }, [activeIndex])

  // A slide being left behind — autoplay advancing past it, or a manual
  // arrow/dot click — always pauses its trailer and rewinds it, and its
  // own "pause" handler below brings its poster back.
  useEffect(() => {
    const prev = prevIndexRef.current
    if (prev !== activeIndex) {
      const video = videoRefs.current[prev]
      if (video) {
        video.pause()
        video.currentTime = 0
      }
    }
    prevIndexRef.current = activeIndex
  }, [activeIndex])

  function clearStall(i: number) {
    const t = stallTimersRef.current[i]
    if (t) {
      window.clearTimeout(t)
      delete stallTimersRef.current[i]
    }
  }

  // Autoplay scheduling: a flat timer for a poster-only slide; a slide
  // with a trailer shows its poster for a lead-in, plays the trailer
  // through once, then holds the poster again before actually advancing.
  useEffect(() => {
    if (items.length <= 1 || pausedByScrollRef.current) return
    const myToken = ++tokenRef.current

    function clear() {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
    function advance() {
      if (tokenRef.current !== myToken) return
      setActiveIndex((i) => (i + 1) % items.length)
    }

    const videoUrl = videoUrls[activeIndex]
    if (!videoUrl) {
      timerRef.current = window.setTimeout(advance, HERO_AUTOPLAY_MS)
    } else {
      timerRef.current = window.setTimeout(() => {
        if (tokenRef.current !== myToken) return
        const video = videoRefs.current[activeIndex]
        if (!video) {
          advance()
          return
        }
        video.currentTime = 0
        video.play().catch(() => {})
        const onEnded = () => {
          video.removeEventListener('ended', onEnded)
          if (tokenRef.current !== myToken) return
          timerRef.current = window.setTimeout(advance, HERO_POSTER_LEAD_MS)
        }
        video.addEventListener('ended', onEnded)
      }, HERO_POSTER_LEAD_MS)
    }

    return () => {
      // Intentionally mutating, not reading, tokenRef here — this is the
      // standard "monotonic cancellation token" pattern (bump it so any
      // in-flight timeout/listener from this run becomes a stale no-op),
      // not the stale-DOM-ref read the lint rule is meant to catch.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      tokenRef.current++
      clear()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex, videoUrls[activeIndex], items.length, scheduleTick])

  // Scrolling away from the top pauses whatever trailer is playing
  // (wasted bandwidth on a hero nobody's looking at); scrolling back
  // resumes the same lead-in-then-play sequence a freshly activated
  // slide gets.
  useEffect(() => {
    function onScroll() {
      const away = window.scrollY > 4
      if (away === pausedByScrollRef.current) return
      pausedByScrollRef.current = away
      if (away) {
        const video = videoRefs.current[activeIndex]
        if (video) {
          video.pause()
          video.currentTime = 0
        }
      }
      setScheduleTick((t) => t + 1)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [activeIndex])

  function goToSlide(i: number) {
    setActiveIndex(((i % items.length) + items.length) % items.length)
  }
  function handleMouseEnter() {
    tokenRef.current++
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }
  function handleMouseLeave() {
    setScheduleTick((t) => t + 1)
    setCursorNear(false)
  }
  function handleMouseMove(e: ReactMouseEvent) {
    // Measured against the logo and the synopsis, not the whole text
    // column they sit in: the column's bounds meant both buttons and all
    // the slack around them counted as "near it" too.
    //
    // While it is hidden the paragraph is still in flow at max-height 0,
    // so this rect collapses to a line exactly where the text is about
    // to appear — which is the right thing to aim at, and makes the zone
    // a band across that position. Revealing it only grows the rect, so
    // the cursor that opened it stays inside: no flicker at the edge.
    const slide = heroRef.current?.querySelector<HTMLElement>('.home-hero-slide.active')
    if (!slide) return
    // The logo and the blurb together, as one region: the meta line
    // between them is swept up with it, which is what makes this an area
    // to reach into rather than two separate targets with a dead gap.
    //
    // The logo is measured by its own image box, not the h1 wrapping it
    // — that h1 is a block spanning the full column, and using it would
    // quietly hand back all the width this is trying not to claim. When
    // there is no logo the h1 *is* the title, so it stands in.
    const boxes = [
      slide.querySelector<HTMLElement>('.hero-logo') ?? slide.querySelector<HTMLElement>('h1'),
      slide.querySelector<HTMLElement>('.hero-syn'),
    ]
      .filter((el): el is HTMLElement => el !== null)
      .map((el) => el.getBoundingClientRect())
    if (!boxes.length) return
    const left = Math.min(...boxes.map((b) => b.left))
    const right = Math.max(...boxes.map((b) => b.right))
    const top = Math.min(...boxes.map((b) => b.top))
    const bottom = Math.max(...boxes.map((b) => b.bottom))
    setCursorNear(
      e.clientX > left - CURSOR_NEAR_PAD_PX &&
        e.clientX < right + CURSOR_NEAR_PAD_PX &&
        e.clientY > top - CURSOR_NEAR_PAD_PX &&
        e.clientY < bottom + CURSOR_NEAR_PAD_PX,
    )
  }

  if (loading) return <HeroSkeleton />
  if (!items.length) return null

  return (
    <div className="home-hero-wrap">
      {/* One glow for the carousel, not one per slide: it is tinted from
          whichever slide is showing and crossfades with it. It holds its
          strength while a trailer plays — it is the page's ground now,
          not a layer inside the pane, and dimming it pulsed the whole
          page dark every time a trailer started. */}
      <HeroGlow items={items} activeIndex={activeIndex} />
      <div className="home-hero" ref={heroRef} onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave} onMouseMove={handleMouseMove}>
        {items.map((item, i) => {
          const isTv = item.mediaType === 'tv'
          const title = item.title || item.name || item.original_title || item.original_name || ''
          const href = isTv ? `#/tv/${item.id}` : `#/movies/${item.id}`
          const onPlex = !!item.on_plex
          const info = enrichment[i]
          const hasVideo = !!videoUrls[i]

          return (
            <div className={`home-hero-slide${i === activeIndex ? ' active' : ''}`} key={item.id}>
                <a className="home-hero-media" href={href} aria-label={title}>
                {/* Portrait key art on a phone, the landscape backdrop
                    everywhere else: the phone card's art well is taller
                    than it is wide, and a 16:9 backdrop cropped into it
                    loses almost everything either side of centre. */}
                <img
                  className={videoVisible[i] ? 'home-hero-poster-hidden' : ''}
                  src={isPhone ? posterUrl(item.poster_path) : backdropUrl(item.backdrop_path)}
                  alt=""
                  loading={i === 0 ? 'eager' : 'lazy'}
                />
                {videoUrls[i] && (
                  <div className="home-hero-video-wrap">
                    <video
                      ref={(el) => {
                        videoRefs.current[i] = el
                      }}
                      src={videoUrls[i] ?? undefined}
                      muted={i === activeIndex ? muted : true}
                      loop={false}
                      playsInline
                      preload="auto"
                      aria-hidden="true"
                      className={videoVisible[i] ? 'home-hero-video-visible' : ''}
                      onPlaying={() => {
                        clearStall(i)
                        setVideoVisible((v) => ({ ...v, [i]: true }))
                      }}
                      onPause={() => {
                        clearStall(i)
                        setVideoVisible((v) => ({ ...v, [i]: false }))
                      }}
                      onError={() => {
                        clearStall(i)
                        setVideoVisible((v) => ({ ...v, [i]: false }))
                      }}
                      onWaiting={() => {
                        if (stallTimersRef.current[i]) return
                        stallTimersRef.current[i] = window.setTimeout(() => {
                          delete stallTimersRef.current[i]
                          setVideoVisible((v) => ({ ...v, [i]: false }))
                        }, 500)
                      }}
                    />
                  </div>
                )}
              </a>
              <div className="home-hero-fade" />
              <div className="home-hero-content">
                <div className="home-hero-body">
                  {/* The title logo when TMDB has one, the text title
                      otherwise; the h1 keeps the name for screen readers
                      either way. */}
                  <h1 className={info?.logo ? 'has-logo' : undefined}>
                    {info?.logo ? <img className="hero-logo" src={info.logo} alt={title} /> : title}
                  </h1>
                  <div className="hero-line">
                    <Icon name={info?.badge ? 'megaphone' : 'chart'} />
                    {info?.badge ?? `#${i + 1} trending this week`}
                  </div>
                  {/* The phone card's dot-separated facts line. Rendered
                      always, shown only under 640px (the desktop frame
                      says the same things through its pills and the cert
                      badge in the corner). */}
                  <div className="home-hero-meta">
                    {[
                      isTv ? 'Show' : 'Movie',
                      info?.genres,
                      yearOf(item.release_date ?? item.first_air_date),
                      info?.length,
                      info?.cert,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                  {item.overview && (
                    <p className={`hero-syn${videoVisible[i] && !cursorNear ? ' hidden-for-video' : ''}`}>{item.overview}</p>
                  )}
                  <div className="home-hero-actions">
                    <a className="btn pri" href={href}>
                      <Icon name={onPlex ? 'play' : 'plus'} />
                      <FlipLabel first={onPlex ? 'On Plex' : 'Not on Plex yet'} second={onPlex ? 'Watch now' : 'Add to Plex'} active={i === activeIndex} />
                    </a>
                    {/* Icon-only on desktop, a full-width labelled button
                        on the phone card — same link either way, so the
                        label is markup the CSS reveals rather than a
                        second control. */}
                    <a className="btn sec circ home-hero-info" href={href} aria-label="More info">
                      <Icon name="info" />
                      <span className="home-hero-info-label">More info</span>
                    </a>
                    {i === activeIndex && hasVideo && (
                      <button
                        className="btn sec circ"
                        aria-label={muted ? 'Unmute trailer' : 'Mute trailer'}
                        onClick={() => setMuted((m) => !m)}
                      >
                        <Icon name={muted ? 'volume-off' : 'volume-on'} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
              {info?.cert && <div className="home-hero-cert">{info.cert}</div>}
            </div>
          )
        })}

        {items.length > 1 && (
          <>
            {/* Grouped rather than positioned apart: they sit together in
                the bottom-right corner now, so the pair is placed once and
                the gap between them is the flex gap, instead of each
                carrying its own edge offset and a width to subtract. */}
            <div className="home-hero-nav">
              <button className="home-hero-arrow" aria-label="Previous slide" onClick={() => goToSlide(activeIndex - 1)}>
                <Icon name="back" />
              </button>
              <button className="home-hero-arrow" aria-label="Next slide" onClick={() => goToSlide(activeIndex + 1)}>
                <Icon name="next" />
              </button>
            </div>
            <div className="home-hero-dots">
              {items.map((item, i) => (
                <button
                  key={item.id}
                  className={`home-hero-dot${i === activeIndex ? ' active' : ''}`}
                  aria-label={`Slide ${i + 1}`}
                  onClick={() => goToSlide(i)}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
