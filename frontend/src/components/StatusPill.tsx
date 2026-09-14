import { statusMeta } from '../lib/status'
import './StatusPill.css'

export default function StatusPill({ status }: { status: string }) {
  const m = statusMeta(status)
  return (
    <span className={`status-pill ${m.cls}`}>
      <span className={`status-dot ${m.cls}`} />
      {m.label}
    </span>
  )
}
