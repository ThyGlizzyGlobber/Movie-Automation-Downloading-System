import './ScoreRing.css'

// Rounded-cap ring via an inline SVG circle (stroke-dasharray/-dashoffset)
// — a plain conic-gradient can't draw rounded ends. r=22 matches the
// ring's own 48px box at stroke-width:4 (22 = (48-4)/2).
export default function ScoreRing({ voteAverage }: { voteAverage?: number | null }) {
  if (!voteAverage) return null
  const pct = Math.round(voteAverage * 10)
  const r = 22
  const circumference = 2 * Math.PI * r
  const offset = circumference * (1 - pct / 100)
  return (
    <div className="score-ring" title={`User Score: ${pct}%`}>
      <svg viewBox="0 0 48 48" aria-hidden="true">
        <circle className="track" cx={24} cy={24} r={r} />
        <circle className="fill" cx={24} cy={24} r={r} strokeDasharray={circumference} strokeDashoffset={offset} />
      </svg>
      <span>{pct}%</span>
    </div>
  )
}
