import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import PosterCard, { type PosterCardItem } from './PosterCard'
import './MediaRow.css'

export default function MediaRow<T extends PosterCardItem>({
  title,
  items,
  mediaType,
  expandHref,
}: {
  title: string
  items: T[]
  mediaType: 'movie' | 'tv' | ((item: T) => 'movie' | 'tv')
  expandHref?: string
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [atStart, setAtStart] = useState(true)

  function handleScroll() {
    const track = trackRef.current
    if (track) setAtStart(track.scrollLeft <= 2)
  }

  // One "page" of the row per click, matching the row's own current
  // width rather than a small nudge. Revolves once already at the end
  // (right arrow loops back to start) — the left arrow is what's hidden
  // at the other end instead, so only this direction needs the wrap.
  function scrollByPage(dir: number) {
    const track = trackRef.current
    if (!track) return
    const atEnd = track.scrollLeft >= track.scrollWidth - track.clientWidth - 2
    if (dir > 0 && atEnd) track.scrollTo({ left: 0, behavior: 'smooth' })
    else track.scrollBy({ left: dir * track.clientWidth, behavior: 'smooth' })
  }

  if (!items.length) return null

  return (
    <section className="row">
      {expandHref ? (
        <Link className="row-title" to={expandHref}>
          {title}
          <span className="row-title-arrow">→</span>
        </Link>
      ) : (
        <h2>{title}</h2>
      )}
      <div className={`hscroll-wrap${atStart ? ' at-start' : ''}`}>
        <div className="hscroll" ref={trackRef} onScroll={handleScroll}>
          {items.map((item) => (
            <PosterCard key={item.id} item={item} mediaType={typeof mediaType === 'function' ? mediaType(item) : mediaType} />
          ))}
        </div>
        <button className="hscroll-arrow hscroll-arrow-left" aria-label="Scroll left" onClick={() => scrollByPage(-1)}>
          <span className="material-symbols-rounded">chevron_left</span>
        </button>
        <button className="hscroll-arrow hscroll-arrow-right" aria-label="Scroll right" onClick={() => scrollByPage(1)}>
          <span className="material-symbols-rounded">chevron_right</span>
        </button>
      </div>
    </section>
  )
}
