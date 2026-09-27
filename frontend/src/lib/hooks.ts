import { useCallback, useEffect, useRef, useState } from 'react'

// Settings' nav shell is the first place behavior (not just styling)
// genuinely differs by breakpoint — mobile starts at a section list,
// desktop always shows a section's panel — so it needs to know the
// breakpoint in JS, not just in CSS.
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mql = window.matchMedia(query)
    const listener = () => setMatches(mql.matches)
    listener()
    mql.addEventListener('change', listener)
    return () => mql.removeEventListener('change', listener)
  }, [query])
  return matches
}

// A value shown for a moment before falling back to `idle`: a Save
// button's "Saved", a quick setting's tick. Setting it again, or leaving
// the page, drops the pending fall-back, so an old timer can't cut a new
// flash short or set state on an unmounted panel.
export function useFlash<T>(idle: T, ms: number) {
  const [value, setValue] = useState<T>(idle)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const set = useCallback((next: T) => {
    window.clearTimeout(timer.current)
    setValue(next)
  }, [])
  const flash = useCallback(
    (next: T) => {
      set(next)
      timer.current = window.setTimeout(() => setValue(idle), ms)
    },
    [set, idle, ms],
  )
  return [value, set, flash] as const
}

export type SaveState = 'idle' | 'saving' | 'saved' | 'error'

// The settings panels' Save button: "Saved" for a beat, then back to Save.
export function useSaveFlash() {
  return useFlash<SaveState>('idle', 1200)
}
