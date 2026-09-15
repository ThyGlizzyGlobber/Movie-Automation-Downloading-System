import { usePosterGlow } from '../lib/palette'
import './AmbientGlow.css'

// Three blurred colour blobs behind hero and detail artwork, tinted
// from the title's poster (lib/palette.ts). Sits inside a positioned,
// overflow-hidden container as its first child so the art and fades
// paint over it. Falls back to the house glow tokens until (or unless)
// the poster yields a hue, with a short crossfade so the colour change
// isn't a pop.
// `dimmed` eases the whole glow down while a trailer plays (the video
// carries the colour then) and back up once it stops.
export default function AmbientGlow({ posterPath, dimmed = false }: { posterPath: string | null | undefined; dimmed?: boolean }) {
  const vars = usePosterGlow(posterPath)
  return (
    <div className={`ambient-glow${vars ? ' tinted' : ''}${dimmed ? ' dimmed' : ''}`} style={vars} aria-hidden="true">
      <i />
      <i />
      <i />
    </div>
  )
}
