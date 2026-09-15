import Icon, { type IconName } from './Icon'
import { useToast, type ToastTone } from '../lib/toast'
import './ToastStack.css'

const TONE_ICON: Record<ToastTone, IconName> = { ok: 'check-circle', error: 'alert', info: 'download' }

export default function ToastStack() {
  const { toasts, dismiss } = useToast()
  if (!toasts.length) return null
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((t) => {
        const tone = t.tone ?? 'info'
        return (
          <div className={`toast ${tone}`} key={t.id}>
            <span className="toast-circ">
              <Icon name={t.icon ?? TONE_ICON[tone]} />
            </span>
            <div className="toast-text">
              <b>{t.title}</b>
              {t.body && <small>{t.body}</small>}
            </div>
            {t.action && (
              <button
                className="toast-action"
                onClick={() => {
                  t.action?.onClick()
                  dismiss(t.id)
                }}
              >
                {t.action.label}
              </button>
            )}
            <button className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <Icon name="close" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
