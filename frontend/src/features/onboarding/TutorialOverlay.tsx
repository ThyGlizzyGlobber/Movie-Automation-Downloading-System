import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSession } from '../auth/useSession'
import { markTutorialSeen } from '../../api/auth'
import './TutorialOverlay.css'

// A short slide-based intro, not element-anchored coach-marks — a
// coach-mark's target element can easily be scrolled off-screen on a
// small viewport, which a plain carousel avoids entirely (Part H).
// Shown once per Plex account (server-side, via has_seen_tutorial — not
// localStorage, so it genuinely shows once regardless of which device a
// person first signs in from).
const SLIDES = [
  { title: 'Welcome to Obsidian', body: 'Find a movie or show and tap Request. Obsidian does the rest.' },
  { title: 'See what’s on the way', body: 'Requests shows everything that’s downloading and everything that’s ready.' },
  { title: 'Shared with the house', body: 'Everyone sees the same requests.' },
]

export default function TutorialOverlay() {
  const session = useSession()
  const queryClient = useQueryClient()
  const [slide, setSlide] = useState(0)
  const [dismissing, setDismissing] = useState(false)

  if (!session.data || session.data.has_seen_tutorial || dismissing) return null

  async function finish() {
    setDismissing(true)
    try {
      await markTutorialSeen()
    } finally {
      queryClient.invalidateQueries({ queryKey: ['session'] })
    }
  }

  const isLast = slide === SLIDES.length - 1
  const current = SLIDES[slide]

  return (
    <div className="tutorial-overlay">
      <div className="tutorial-card">
        <button className="tutorial-skip" onClick={finish}>
          Skip
        </button>
        <h2 className="tutorial-title">{current.title}</h2>
        <p className="tutorial-body">{current.body}</p>
        <div className="tutorial-dots">
          {SLIDES.map((_, i) => (
            <span key={i} className={`tutorial-dot${i === slide ? ' active' : ''}`} />
          ))}
        </div>
        <button className="tutorial-next" onClick={() => (isLast ? finish() : setSlide((s) => s + 1))}>
          {isLast ? 'Got it' : 'Next'}
        </button>
      </div>
    </div>
  )
}
