import type { SVGProps } from 'react'
import './Icon.css'

// The Obsidian icon set: one family of 24-unit glyphs drawn as 1.75px
// round-capped strokes (play and the rating star are filled), matching
// the reference's icon sheet in design-exploration/obsidian.html. Sized
// by font-size like the icon font it replaces (1em square), so the
// existing per-placement font-size rules keep working. Decorative by
// default; pass a `label` when an icon is the only content of a control
// without its own aria-label.

const GLYPHS = {
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.3-4.3" /></>,
  user: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="10" r="3" /><path d="M6.6 18.4a6 6 0 0 1 10.8 0" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>,
  back: <path d="M15 5l-7 7 7 7" />,
  next: <path d="M9 5l7 7-7 7" />,
  arrow: <path d="M4 12h16M13 5l7 7-7 7" />,
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  check: <path d="M4.5 12.5l5 5L19.5 7" />,
  'check-circle': <><circle cx="12" cy="12" r="8.5" /><path d="M8.5 12.5l2.5 2.5 5-5.5" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8v.3" /></>,
  alert: <><path d="M12 3.5 21.5 20h-19z" /><path d="M12 10v4.5M12 17.5v.3" /></>,
  'alert-circle': <><circle cx="12" cy="12" r="8.5" /><path d="M12 8v5M12 16.5v.3" /></>,
  block: <><circle cx="12" cy="12" r="8.5" /><path d="M6 6l12 12" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  loader: <path d="M12 3a9 9 0 1 1-6.4 2.6" />,
  download: <path d="M12 3v12M6.5 10.5 12 16l5.5-5.5M4 20h16" />,
  play: <path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none" />,
  pause: <path d="M8 5v14M16 5v14" />,
  home: <path d="M3.5 11 12 3.5l8.5 7.5v9.5h-6v-6h-5v6h-6z" />,
  film: <><rect x="3" y="3.5" width="18" height="17" rx="1.5" /><path d="M7 3.5v17M17 3.5v17M3 8h4M3 12h4M3 16h4M17 8h4M17 12h4M17 16h4" /></>,
  tv: <><rect x="2.5" y="6.5" width="19" height="12.5" rx="2" /><path d="M8.5 2.5 12 6l3.5-3.5M9 22h6" /></>,
  star: <path d="M12 3.2l2.7 5.7 6.2.8-4.6 4.3 1.2 6.2L12 17.2l-5.5 3 1.2-6.2L3.1 9.7l6.2-.8z" fill="currentColor" stroke="none" />,
  megaphone: <><path d="M3 10.5v3a1 1 0 0 0 1 1h2.5l5 3.5V6L6.5 9.5H4a1 1 0 0 0-1 1z" /><path d="M15 9.5a3.5 3.5 0 0 1 0 5M17.8 7a7 7 0 0 1 0 10" /></>,
  'volume-on': <><path d="M4 10v4h3l4 3.5v-11L7 10z" /><path d="M15 9.5a3.5 3.5 0 0 1 0 5M17.8 7a7 7 0 0 1 0 10" /></>,
  'volume-off': <><path d="M4 10v4h3l4 3.5v-11L7 10z" /><path d="M16 9.5l5 5M21 9.5l-5 5" /></>,
  drive: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 13h18M7 16.3h.3M11 16.3h.3" /></>,
  sliders: <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1.5 14h5M9.5 8h5M17.5 16h5" />,
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  plug: <path d="M9 2.5v5M15 2.5v5M6 7.5h12v3a6 6 0 0 1-12 0zM12 16.5v5" />,
  server: <><rect x="3" y="3.5" width="18" height="7" rx="2" /><rect x="3" y="13.5" width="18" height="7" rx="2" /><path d="M7 7h.3M7 17h.3" /></>,
  lock: <><rect x="4" y="10.5" width="16" height="10.5" rx="2.5" /><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5" /></>,
  chart: <path d="M4 20h16M6 16l4-5 4 3 5-7" />,
  refresh: <path d="M20 12a8 8 0 1 1-2.4-5.7M20 4v5h-5" />,
  plex: <path d="M6 3h6l6 9-6 9H6l6-9z" />,
  grid: <><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></>,
  more: <path d="M5 12h.3M12 12h.3M19 12h.3" />,
  hd: <><rect x="2.5" y="5.5" width="19" height="13" rx="2" /><path d="M7 9v6M7 12h3.5M10.5 9v6M14 9v6h2a3 3 0 0 0 0-6z" /></>,
  // Viewfinder corners with a line that sweeps top to bottom (Icon.css)
  // wherever the icon appears: the "searching" state site-wide.
  scan: (
    <>
      <path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
      <path className="scan-line" d="M4 12h16" />
    </>
  ),
} as const

export type IconName = keyof typeof GLYPHS

export default function Icon({
  name,
  label,
  className,
  ...rest
}: { name: IconName; label?: string; className?: string } & Omit<SVGProps<SVGSVGElement>, 'name'>) {
  return (
    <svg
      className={`icon icon-${name}${className ? ` ${className}` : ''}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
      {...rest}
    >
      {GLYPHS[name]}
    </svg>
  )
}
