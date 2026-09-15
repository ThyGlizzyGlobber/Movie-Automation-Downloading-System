import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { profileUrl } from '../lib/tmdbImage'
import type { CastMember } from '../types/movies'
import './MediaRow.css'
import './CastRow.css'
import Icon from './Icon'

// The "Top Billed Cast" photo-card row at the bottom of a detail page —
// shares the same hscroll shell/scroll-arrow behavior as MediaRow's
// poster rows (see MediaRow.css), just with circular photo cards instead
// of posters.
export default function CastRow({ cast }: { cast?: CastMember[] }) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [atStart, setAtStart] = useState(true)

  function handleScroll() {
    const track = trackRef.current
    if (track) setAtStart(track.scrollLeft <= 2)
  }

  function scrollByPage(dir: number) {
    const track = trackRef.current
    if (!track) return
    const atEnd = track.scrollLeft >= track.scrollWidth - track.clientWidth - 2
    if (dir > 0 && atEnd) track.scrollTo({ left: 0, behavior: 'smooth' })
    else track.scrollBy({ left: dir * track.clientWidth, behavior: 'smooth' })
  }

  const top = (cast || []).slice(0, 15)
  if (!top.length) return null

  return (
    <section className="cast-section row">
      <h2 className="section-heading">Top Billed Cast</h2>
      <div className={`hscroll-wrap${atStart ? ' at-start' : ''}`}>
        <div className="hscroll" ref={trackRef} onScroll={handleScroll}>
          {top.map((c) => (
            <Link key={c.id} className="cast-card" to={`/person/${c.id}`}>
              <img src={profileUrl(c.profile_path)} alt="" loading="lazy" />
              <div className="cast-name">{c.name}</div>
              <div className="cast-character">{c.character || ''}</div>
            </Link>
          ))}
        </div>
        <button className="hscroll-arrow hscroll-arrow-left" aria-label="Scroll left" onClick={() => scrollByPage(-1)}>
          <Icon name="back" />
        </button>
        <button className="hscroll-arrow hscroll-arrow-right" aria-label="Scroll right" onClick={() => scrollByPage(1)}>
          <Icon name="next" />
        </button>
      </div>
    </section>
  )
}
