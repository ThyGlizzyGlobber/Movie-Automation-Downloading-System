import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import type { IconName } from '../components/Icon'

export type ToastTone = 'ok' | 'error' | 'info'

export interface ToastOptions {
  title: string
  body?: string
  tone?: ToastTone
  icon?: IconName
  /* An optional action at the right edge (the reference's "Play" /
     "Retry"). */
  action?: { label: string; onClick: () => void }
}

export interface ToastItem extends ToastOptions {
  id: number
}

interface ToastContextValue {
  toasts: ToastItem[]
  toast: (opts: ToastOptions) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)
const TOAST_MS = 5000

export function ToastProvider({ children }: { children: ReactNode }) {
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

  const value = useMemo(() => ({ toasts, toast, dismiss }), [toasts, toast, dismiss])
  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>
}

// Glass notices that slide in at the bottom corner (the reference's
// toasts) in place of browser alert() dialogs.
export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}

export function errorText(err: unknown, fallback = 'Something went wrong'): string {
  return err instanceof Error && err.message ? err.message : fallback
}
