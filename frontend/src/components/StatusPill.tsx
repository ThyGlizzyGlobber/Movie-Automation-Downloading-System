import { statusMeta } from '../lib/status'
import Icon from './Icon'
import './StatusPill.css'

export default function StatusPill({ status }: { status: string }) {
  const m = statusMeta(status)
  return (
    <span className={`status-pill ${m.cls}`}>
      <Icon name={m.icon} className="status-icon" />
      {m.label}
    </span>
  )
}
