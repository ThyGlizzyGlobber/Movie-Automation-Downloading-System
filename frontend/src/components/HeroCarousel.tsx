import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { getMovie, getMovieTrailer } from '../api/movies'
import { getTvShow, getTvTrailer } from '../api/tv'
import { backdropUrl, logoUrl } from '../lib/tmdbImage'
import { movieHeroBadge, movieCertOf, tvHeroBadge, tvCertOf, type TaggedItem } from '../lib/homeHero'
import AmbientGlow from './AmbientGlow'
import './HeroCarousel.css'
import Icon from './Icon'

const HERO_TRAILER_COUNT = 2 // only the front slides ever get a background video
const HERO_AUTOPLAY_MS = 7000 // flat dwell time for a poster-only slide
// How long a trailer slide's poster shows on its own — both before the
// trailer starts and again after it finishes, before actually advancing.
const HERO_POSTER_LEAD_MS = 3000

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

export default function HeroCarousel({ items }: { items: TaggedItem[] }) {
  const [activeIndex, setActiveIndex] = useState(0)
  const [muted, setMuted] = useState(true)
  const [videoUrls, setVideoUrls] = useState<Record<number, string | null>>({})
  const [videoVisible, setVideoVisible] = useState<Record<number, boolean>>({})
  const [enrichment, setEnrichment] = useState<Record<number, Enrichment>>({})
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

  // Trailer fetch for the front couple of slides only — self-hosted,
  // chromeless <video>, never autoplaying on its own; playback is
  // entirely driven by the scheduling effect below, gated on the slide
  // actually being active.
  useEffect(() => {
    let cancelled = false
    items.slice(0, HERO_TRAILER_COUNT).forEach(async (item, i) => {
      try {
        const { url } = item.mediaType === 'tv' ? await getTvTrailer(item.id) : await getMovieTrailer(item.id)
        if (!cancelled) setVideoUrls((prev) => ({ ...prev, [i]: url }))
      } catch {
        // no trailer on file — the poster stays as the slide's art
      }
    })
    return () => {
      cancelled = true
    }
  }, [items])

  // Per-slide status badge + age-rating pill — needs the full detail
  // call (TMDB's list/trending items carry neither).
  useEffect(() => {
    let cancelled = false
    items.forEach(async (item, i) => {
      try {
        let badge: string | null
        let cert: string
        let length: string
        let genres: string
        let logo: string | null
        if (item.mediaType === 'tv') {
          const detail = await getTvShow(item.id)
          badge = tvHeroBadge(detail)
          cert = tvCertOf(detail)
          const seasons = (detail.seasons ?? []).filter((se) => se.season_number > 0).length
          length = seasons ? `${seasons} season${seasons === 1 ? '' : 's'}` : ''
          genres = (detail.genres ?? []).slice(0, 2).map((g) => g.name).join(' · ')
          logo = logoUrl(detail.logo_path)
        } else {
          const detail = await getMovie(item.id)
          badge = movieHeroBadge(detail)
          cert = movieCertOf(detail)
          length = movieLength(detail.runtime)
          genres = (detail.genres ?? []).slice(0, 2).map((g) => g.name).join(' · ')
          logo = logoUrl(detail.logo_path)
        }
        if (cancelled) return
        setEnrichment((prev) => ({ ...prev, [i]: { badge, cert, length, genres, logo } }))
      } catch {
        // badge/cert just stay empty
      }
    })
    return () => {
      cancelled = true
    }
  }, [items])

  // While the active slide's trailer is showing, the nav tab tucks up
  // to a sliver (Topbar.css's body.trailer-playing rules) and drops
  // back when the cursor comes near the top.
  const trailerShowing = !!videoVisible[activeIndex]
  useEffect(() => {
    document.body.classList.toggle('trailer-playing', trailerShowing)
    return () => document.body.classList.remove('trailer-playing')
  }, [trailerShowing])

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
    const body = heroRef.current?.querySelector<HTMLElement>('.home-hero-slide.active .home-hero-body')
    if (!body) return
    const r = body.getBoundingClientRect()
    const pad = 96
    setCursorNear(e.clientX > r.left - pad && e.clientX < r.right + pad && e.clientY > r.top - pad && e.clientY < r.bottom + pad)
  }

  if (!items.length) return null

  return (
    <div className="home-hero" ref={heroRef} onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave} onMouseMove={handleMouseMove}>
      {items.map((item, i) => {
        const isTv = item.mediaType === 'tv'
        const title = item.title || item.name || item.original_title || item.original_name || ''
        const href = isTv ? `#/tv/${item.id}` : `#/movies/${item.id}`
        const onPlex = !!item.on_plex
        const info = enrichment[i]
        const hasVideo = i < HERO_TRAILER_COUNT && !!videoUrls[i]

        return (
          <div className={`home-hero-slide${i === activeIndex ? ' active' : ''}`} key={item.id}>
            <AmbientGlow posterPath={item.poster_path} dimmed={!!videoVisible[i]} />
            <a className="home-hero-media" href={href} aria-label={title}>
              <img
                className={videoVisible[i] ? 'home-hero-poster-hidden' : ''}
                src={backdropUrl(item.backdrop_path)}
                alt=""
                loading={i === 0 ? 'eager' : 'lazy'}
              />
              {i < HERO_TRAILER_COUNT && videoUrls[i] && (
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
                {item.overview && (
                  <p className={`hero-syn${videoVisible[i] && !cursorNear ? ' hidden-for-video' : ''}`}>{item.overview}</p>
                )}
                <div className="home-hero-actions">
                  <a className="btn pri" href={href}>
                    <Icon name={onPlex ? 'play' : 'plus'} />
                    <FlipLabel first={onPlex ? 'On Plex' : 'Not on Plex yet'} second={onPlex ? 'Watch now' : 'Request'} active={i === activeIndex} />
                  </a>
                  <a className="btn sec circ" href={href} aria-label="More info">
                    <Icon name="info" />
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
          <button className="home-hero-arrow home-hero-arrow-left" aria-label="Previous slide" onClick={() => goToSlide(activeIndex - 1)}>
            <Icon name="back" />
          </button>
          <button className="home-hero-arrow home-hero-arrow-right" aria-label="Next slide" onClick={() => goToSlide(activeIndex + 1)}>
            <Icon name="next" />
          </button>
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
  )
}
