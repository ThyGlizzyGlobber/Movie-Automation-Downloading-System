import { useEffect, useRef, useState } from 'react'
import { getMovie, getMovieTrailer } from '../api/movies'
import { getTvShow, getTvTrailer } from '../api/tv'
import { backdropUrl } from '../lib/tmdbImage'
import { movieHeroBadge, movieCertOf, tvHeroBadge, tvCertOf, type TaggedItem } from '../lib/homeHero'
import AmbientGlow from './AmbientGlow'
import './HeroCarousel.css'
import Icon from './Icon'

const HERO_TRAILER_COUNT = 2 // only the front slides ever get a background video
const HERO_AUTOPLAY_MS = 7000 // flat dwell time for a poster-only slide
// How long a trailer slide's poster shows on its own — both before the
// trailer starts and again after it finishes, before actually advancing.
const HERO_POSTER_LEAD_MS = 3000

interface Enrichment {
  badge: string | null
  cert: string
}

export default function HeroCarousel({ items }: { items: TaggedItem[] }) {
  const [activeIndex, setActiveIndex] = useState(0)
  const [muted, setMuted] = useState(true)
  const [videoUrls, setVideoUrls] = useState<Record<number, string | null>>({})
  const [videoVisible, setVideoVisible] = useState<Record<number, boolean>>({})
  const [enrichment, setEnrichment] = useState<Record<number, Enrichment>>({})
  const [scheduleTick, setScheduleTick] = useState(0)

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
        if (item.mediaType === 'tv') {
          const detail = await getTvShow(item.id)
          badge = tvHeroBadge(detail)
          cert = tvCertOf(detail)
        } else {
          const detail = await getMovie(item.id)
          badge = movieHeroBadge(detail)
          cert = movieCertOf(detail)
        }
        if (cancelled) return
        setEnrichment((prev) => ({ ...prev, [i]: { badge, cert } }))
      } catch {
        // badge/cert just stay empty
      }
    })
    return () => {
      cancelled = true
    }
  }, [items])

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
  }

  if (!items.length) return null

  return (
    <div className="home-hero" onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      {items.map((item, i) => {
        const isTv = item.mediaType === 'tv'
        const title = item.title || item.name || item.original_title || item.original_name || ''
        const href = isTv ? `#/tv/${item.id}` : `#/movies/${item.id}`
        const onPlex = !!item.on_plex
        const ctaIcon = onPlex ? 'play' : 'plus'
        const ctaLabel = onPlex ? 'Watch on Plex' : 'Add to Plex'
        const info = enrichment[i]
        const hasVideo = i < HERO_TRAILER_COUNT && !!videoUrls[i]

        return (
          <div className={`home-hero-slide${i === activeIndex ? ' active' : ''}`} key={item.id}>
            <AmbientGlow posterPath={item.poster_path} />
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
              <div className="home-hero-info">
                {info?.badge && (
                  <div className="home-hero-badge">
                    <Icon name="megaphone" />
                    {info.badge}
                  </div>
                )}
                <h1>{title}</h1>
                <div className="home-hero-actions">
                  <a className="add-btn" href={href}>
                    <Icon name={ctaIcon} />
                    {ctaLabel}
                  </a>
                  <a className="home-hero-info-btn" href={href} aria-label="More info">
                    <Icon name="info" />
                  </a>
                  {i === activeIndex && hasVideo && (
                    <button
                      className="home-hero-mute-btn"
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
