import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { ToastContext, ToastListContext, type ToastItem, type ToastOptions } from '../lib/toast'

const TOAST_MS = 5000

// Up to four on screen at once, each leaving on its own after five
// seconds unless dismissed first.
export default function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    (opts: ToastOptions) => {
      const id = nextId.current++
      setToasts((list) => [...list.slice(-3), { ...opts, id }])
      window.setTimeout(() => dismiss(id), TOAST_MS)
    },
    [dismiss],
  )

  const actions = useMemo(() => ({ toast, dismiss }), [toast, dismiss])
  return (
    <ToastContext.Provider value={actions}>
      <ToastListContext.Provider value={toasts}>{children}</ToastListContext.Provider>
    </ToastContext.Provider>
  )
}
