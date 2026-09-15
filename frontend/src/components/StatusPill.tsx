import { statusMeta } from '../lib/status'
import './StatusPill.css'

export default function StatusPill({ status }: { status: string }) {
  const m = statusMeta(status)
  return (
    <span className={`status-pill ${m.cls}`}>
      <span className="material-symbols-rounded status-icon" aria-hidden="true">
        {m.icon}
      </span>
      {m.label}
    </span>
  )
}
