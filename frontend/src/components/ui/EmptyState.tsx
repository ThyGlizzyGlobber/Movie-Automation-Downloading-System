import type { ReactNode } from 'react'
import Icon, { type IconName } from './Icon'
import './StateMessage.css'

// The reference's empty state: a glass card with a circle icon, a light
// title and one plain sentence, plus an optional action.
export default function EmptyState({
  message,
  title,
  icon = 'search',
  action,
}: {
  message: string
  title?: string
  icon?: IconName
  action?: ReactNode
}) {
  return (
    <div className="state-card">
      <span className="state-circ">
        <Icon name={icon} />
      </span>
      {title && <h4>{title}</h4>}
      <p>{message}</p>
      {action}
    </div>
  )
}
