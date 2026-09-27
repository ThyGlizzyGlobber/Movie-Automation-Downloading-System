import type { ReactNode } from 'react'

// One line of a settings card: a bold label with a small description on
// the left, the control on the right (the reference's key rows).
export default function SettingRow({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="setting-row">
      <div className="setting-row-text">
        {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <b>{label}</b>}
        {hint && <small>{hint}</small>}
      </div>
      <div className="setting-row-control">{children}</div>
    </div>
  )
}
