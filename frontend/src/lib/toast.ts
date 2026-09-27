import { createContext, useContext } from 'react'
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

export interface ToastActions {
  toast: (opts: ToastOptions) => void
  dismiss: (id: number) => void
}

// Two contexts so the pages that only ever raise a toast don't re-render
// every time one appears or leaves; only ToastStack reads the list.
// ToastProvider (components/ToastProvider.tsx) fills them.
export const ToastContext = createContext<ToastActions | null>(null)
export const ToastListContext = createContext<ToastItem[]>([])

// Glass notices that slide in at the bottom corner (the reference's
// toasts) in place of browser alert() dialogs.
export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}

export function useToastList(): ToastItem[] {
  return useContext(ToastListContext)
}

export function errorText(err: unknown, fallback = 'Something went wrong'): string {
  return err instanceof Error && err.message ? err.message : fallback
}
