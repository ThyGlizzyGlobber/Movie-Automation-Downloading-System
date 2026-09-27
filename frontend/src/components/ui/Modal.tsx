import { useEffect, type ReactNode } from 'react'
import './Modal.css'

// The blurred backdrop every card-over-the-page shares, and the two ways
// out of one: Escape, or a click on the backdrop around the card.
export default function Modal({
  onClose,
  className,
  labelledBy,
  children,
}: {
  onClose: () => void
  className: string
  labelledBy?: string
  children: ReactNode
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className={className} role="dialog" aria-modal="true" aria-labelledby={labelledBy}>
        {children}
      </div>
    </div>
  )
}
