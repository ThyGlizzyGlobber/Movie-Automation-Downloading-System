import './ProgressRing.css'

// A live download fraction as a glowing ring with the percentage inside
// — the reference's progress treatment for the Requests list, where a
// row is short and wide and a bar under the title read as clutter. Same
// SVG dasharray technique as ScoreRing. ProgressBar remains the thin
// inline form for places with vertical room.
export default function ProgressRing({ progress, size = 44 }: { progress: number; size?: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, progress)) * 100)
  const stroke = 3.5
  const r = (size - stroke) / 2
  const circumference = 2 * Math.PI * r
  const offset = circumference * (1 - pct / 100)
  return (
    <div
      className="progress-ring"
      style={{ width: size, height: size }}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <svg viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle className="track" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} />
        <circle
          className="fill"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <span>{pct}%</span>
    </div>
  )
}
