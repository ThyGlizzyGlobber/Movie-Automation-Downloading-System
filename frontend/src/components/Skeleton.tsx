import type { CSSProperties, ReactNode } from 'react'
import { MediaRowHeading } from './MediaRow'

// Loading placeholders (styles/skeleton.css). Pages build their loading
// state from their own markup with these in the gaps, so the skeleton
// takes the loaded layout's size at every width.

type Width = number | string

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

/* A poster card as the rows and grids draw it: art, title, meta line. */
export function PosterCardSkeleton({ caption = true }: { caption?: boolean }) {
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
          <small>
            <SkelText width="48%" />
          </small>
        </div>
      )}
    </div>
  )
}

/* A poster row with its real heading and shimmer cards. */
export function MediaRowSkeleton({ title, qualifier, count = 10 }: { title: string; qualifier?: string; count?: number }) {
  return (
    <section className="row" aria-busy="true">
      <h2>
        <MediaRowHeading title={title} qualifier={qualifier} />
      </h2>
      <div className="hscroll-wrap">
        <div className="hscroll">
          {Array.from({ length: count }, (_, i) => (
            <PosterCardSkeleton key={i} />
          ))}
        </div>
      </div>
    </section>
  )
}
