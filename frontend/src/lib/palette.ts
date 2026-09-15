import { useEffect, useState, type CSSProperties } from 'react'
import { sampleUrl } from './tmdbImage'

// Poster-derived ambient glow: the three blobs take the poster's three
// main colours (see dominantColours), sampled from the real TMDB poster
// on a small canvas. Everything is best effort: no
// poster, a CORS-tainted canvas, or an unsupported browser all fall
// back to the house glow colours in tokens.css.
//
// The sample is fetched at TMDB's smallest poster size (w92) rather
// than the w342 the cards use: a 24px sample needs no more, and it must
// be a URL the page hasn't already loaded without CORS — the browser
// can otherwise hand the CORS request that cached, header-less copy
// and the load fails outright (confirmed live in Chrome).

const SAMPLE_SIZE = 32
const paletteCache = new Map<string, Promise<Swatch[] | null>>()

export interface Swatch {
  h: number
  s: number
  l: number
  weight: number
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255
  g /= 255
  b /= 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h: number
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
  else if (max === g) h = ((b - r) / d + 2) / 6
  else h = ((r - g) / d + 4) / 6
  return [h * 360, s, l]
}

// The poster's three main colours. Pixels that aren't near-black or
// near-white are binned by hue (24 bins); each bin keeps its pixel
// count and its mean saturation and lightness. Bins are ranked by
// count × (a floor plus saturation), so a large soft area (a sky-blue
// wash) still wins over a tiny vivid accent, and the top three that
// sit at least two bins apart come back. A mostly grey poster yields
// low-saturation swatches, so its glow reads grey-blue, not neon.
function dominantColours(img: HTMLImageElement): Swatch[] {
  const canvas = document.createElement('canvas')
  canvas.width = SAMPLE_SIZE
  canvas.height = SAMPLE_SIZE
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return []
  ctx.drawImage(img, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE)
  const { data } = ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE)
  const bins = 24
  const count = new Array<number>(bins).fill(0)
  const hueSum = new Array<number>(bins).fill(0)
  const satSum = new Array<number>(bins).fill(0)
  const lightSum = new Array<number>(bins).fill(0)
  let greyCount = 0
  let greyLight = 0
  // Near-greys keep a faint hue (a grey sky is still blue-grey); it is
  // averaged as a vector so hues either side of 0° do not cancel.
  let greyX = 0
  let greyY = 0
  let greySat = 0
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 128) continue
    const [h, sat, l] = rgbToHsl(data[i], data[i + 1], data[i + 2])
    if (l < 0.08 || l > 0.94) continue
    if (sat < 0.12) {
      greyCount++
      greyLight += l
      greySat += sat
      greyX += Math.cos((h * Math.PI) / 180) * sat
      greyY += Math.sin((h * Math.PI) / 180) * sat
      continue
    }
    const bin = Math.floor(h / (360 / bins)) % bins
    count[bin]++
    hueSum[bin] += h
    satSum[bin] += sat
    lightSum[bin] += l
  }
  const ranked = count
    .map((n, b) => ({
      bin: b,
      n,
      score: n * (0.35 + satSum[b] / Math.max(1, n)),
      h: hueSum[b] / Math.max(1, n),
      s: satSum[b] / Math.max(1, n),
      l: lightSum[b] / Math.max(1, n),
    }))
    .filter((x) => x.n > 0)
    .sort((a, b) => b.score - a.score)
  const chosen: Swatch[] = []
  for (const x of ranked) {
    if (chosen.length === 3) break
    const far = chosen.every((c) => {
      const d = Math.abs(c.h - x.h)
      return Math.min(d, 360 - d) >= 30
    })
    if (far) chosen.push({ h: x.h, s: x.s, l: x.l, weight: x.n })
  }
  // A poster that is mostly greys gets a muted swatch in its own
  // grey's tint (blue-grey, warm grey), and when greys are the bulk of
  // the image that swatch leads, so the glow follows the poster instead
  // of amplifying a small accent into neon.
  const total = count.reduce((a, b) => a + b, 0) + greyCount
  const greyShare = greyCount / Math.max(1, total)
  if (greyCount > 0 && greyShare > 0.3) {
    // A grey with no real tint of its own reads as cool blue-grey on a
    // black ground; only a measurable cast (a sepia or teal grade) is
    // followed.
    const meanGreySat = greySat / greyCount
    const greyHue = meanGreySat >= 0.07 ? ((Math.atan2(greyY, greyX) * 180) / Math.PI + 360) % 360 : 215
    const grey: Swatch = { h: greyHue, s: Math.max(0.1, meanGreySat), l: greyLight / greyCount, weight: greyCount }
    if (greyShare > 0.5) chosen.unshift(grey)
    else chosen.push(grey)
  }
  return chosen.slice(0, 3)
}

export function posterPalette(posterPath: string | null | undefined): Promise<Swatch[] | null> {
  if (!posterPath) return Promise.resolve(null)
  const url = sampleUrl(posterPath)
  let cached = paletteCache.get(url)
  if (!cached) {
    cached = new Promise<Swatch[] | null>((resolve) => {
      const img = new Image()
      img.crossOrigin = 'anonymous'
      img.onload = () => {
        try {
          const swatches = dominantColours(img)
          resolve(swatches.length ? swatches : null)
        } catch {
          resolve(null) // tainted canvas or similar — keep the defaults
        }
      }
      img.onerror = () => resolve(null)
      img.src = url
    })
    paletteCache.set(url, cached)
  }
  return cached
}

function glow(sw: Swatch): string {
  const h = Math.round(((sw.h % 360) + 360) % 360)
  // Saturation follows the poster within a band that still glows; the
  // lightness is pinned so blobs never wash out or vanish.
  const s = Math.round(Math.min(78, Math.max(14, sw.s * 100 * 1.15)))
  const l = Math.round(Math.min(52, Math.max(34, 30 + sw.l * 26)))
  return `hsl(${h} ${s}% ${l}%)`
}

export function glowVarsForPalette(swatches: Swatch[]): CSSProperties {
  const [a, b, c] = swatches
  const second = b ?? { ...a, h: a.h + 36 }
  const third = c ?? { ...a, h: a.h - 36, s: a.s * 0.8 }
  return { '--g1': glow(a), '--g2': glow(second), '--g3': glow(third) } as CSSProperties
}

// Returns inline CSS variables for the three glow colours once the
// poster has been sampled, or undefined (house defaults) until then
// and whenever sampling isn't possible.
export function usePosterGlow(posterPath: string | null | undefined): CSSProperties | undefined {
  const [vars, setVars] = useState<CSSProperties | undefined>(undefined)
  useEffect(() => {
    let cancelled = false
    setVars(undefined)
    posterPalette(posterPath).then((swatches) => {
      if (!cancelled && swatches) setVars(glowVarsForPalette(swatches))
    })
    return () => {
      cancelled = true
    }
  }, [posterPath])
  return vars
}
