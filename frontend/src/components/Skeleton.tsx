import type { CSSProperties, ReactNode } from 'react'

// Loading placeholders (styles/skeleton.css). Pages build their loading
// state from their own markup with these in the gaps, so the skeleton
// takes the loaded layout's size at every width.

type Width = number | string

// Sample text of typical length for text still to come: a synopsis of
// about 300 characters.
export const SAMPLE_SYNOPSIS =
  'A reluctant hero is pulled back into a world they left behind when an old friend goes missing, and every step toward the truth costs something. Old loyalties are tested, a city keeps its secrets, and the only way out is through the people they swore never to trust again before it is too late.'

function widthStyle(width?: Width): CSSProperties | undefined {
  if (width == null) return undefined
  return { width: typeof width === 'number' ? `${width}px` : width }
}

/* A shimmer block. `className` supplies the shape (a poster, a button). */
export function Skel({ className, width, children }: { className?: string; width?: Width; children?: ReactNode }) {
  return (
    <span className={`skel${className ? ` ${className}` : ''}`} style={widthStyle(width)} aria-hidden="true">
      {children}
    </span>
  )
}

/* One line of text, as tall as a line of the text around it. */
export function SkelText({ width = '100%', inline = false, center = false }: { width?: Width; inline?: boolean; center?: boolean }) {
  return <span className={`skel skel-text${inline ? ' inline' : ''}${center ? ' center' : ''}`} style={widthStyle(width)} aria-hidden="true" />
}

/* Sample text of a typical length, wrapping like the real text will. */
export function SkelWords({ text }: { text: string }) {
  return (
    <span className="skel skel-words" aria-hidden="true">
      {text}
    </span>
  )
}

/* A poster card as the rows and grids draw it: art, title, and the
   "year · genre" line unless the row's cards have none. */
export function PosterCardSkeleton({ caption = true, meta = true }: { caption?: boolean; meta?: boolean }) {
  return (
    <div className="poster-card" aria-hidden="true">
      <div className="poster-art">
        <Skel className="skel-poster" />
      </div>
      {caption && (
        <div className="poster-caption">
          <b>
            <SkelText width="78%" />
          </b>
          {meta && (
            <small>
              <SkelText width="48%" />
            </small>
          )}
        </div>
      )}
    </div>
  )
}
