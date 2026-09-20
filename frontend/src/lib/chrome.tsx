import { useEffect, type RefObject } from 'react'

// There was a `hasHero` context here, toggling a `has-hero` class on
// <body> that twenty-five pages opted into. Nothing styled it: the
// Topbar.css rules its comments described had already gone, so the
// class was set and cleared on every navigation to no effect. Removed
// along with the provider and the per-page calls. The top bar is a
// floating glass pill on every page now, hero or not, and the hero is
// an inset pane that never runs under it.

// Ports setChrome({title})'s document.title job.
export function usePageTitle(title: string | null) {
  useEffect(() => {
    document.title = title ? `${title} — Obsidian` : 'Obsidian'
    return () => {
      document.title = 'Obsidian'
    }
  }, [title])
}

// #topbar is fixed, so it reserves no space in normal flow — main's own
// top padding stands in for that space instead (see global.css), kept
// in sync with the topbar's real rendered height via --topbar-h.
export function useTopbarHeight(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => document.documentElement.style.setProperty('--topbar-h', `${el.offsetHeight}px`)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    if (document.fonts?.ready) document.fonts.ready.then(update)
    return () => observer.disconnect()
  }, [ref])
}
