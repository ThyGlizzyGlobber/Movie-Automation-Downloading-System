import { useEffect, useState, type CSSProperties } from 'react'
import { sampleUrl } from './tmdbImage'

// Poster-derived ambient glow. The reference (design-exploration/
// obsidian.html) tints each hero/detail page from its own artwork: the
// main glow takes the poster's dominant hue, the second sits ~48°
// warmer, the third ~60° cooler. Here the hue comes from sampling the
// real TMDB poster on a small canvas. Everything is best effort: no
// poster, a CORS-tainted canvas, or an unsupported browser all fall
// back to the house glow colours in tokens.css.
//
// The sample is fetched at TMDB's smallest poster size (w92) rather
// than the w342 the cards use: a 24px sample needs no more, and it must
// be a URL the page hasn't already loaded without CORS — the browser
// can otherwise hand the CORS request that cached, header-less copy
// and the load fails outright (confirmed live in Chrome).

const SAMPLE_SIZE = 24
const hueCache = new Map<string, Promise<number | null>>()

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

// Dominant hue of an image: pixels that are reasonably saturated and
// neither near-black nor near-white are binned by hue (15° bins),
// weighted by saturation so a vivid accent outweighs a large muddy
// area. The winning bin's saturation-weighted mean hue is returned.
function dominantHue(img: HTMLImageElement): number | null {
  const canvas = document.createElement('canvas')
  canvas.width = SAMPLE_SIZE
  canvas.height = SAMPLE_SIZE
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  ctx.drawImage(img, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE)
  const { data } = ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE)
  const bins = 24
  const weight = new Array<number>(bins).fill(0)
  const hueSum = new Array<number>(bins).fill(0)
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 128) continue
    const [h, s, l] = rgbToHsl(data[i], data[i + 1], data[i + 2])
    if (s < 0.22 || l < 0.12 || l > 0.9) continue
    const bin = Math.floor(h / (360 / bins)) % bins
    weight[bin] += s
    hueSum[bin] += h * s
  }
  let best = -1
  let bestWeight = 0
  for (let b = 0; b < bins; b++) {
    if (weight[b] > bestWeight) {
      bestWeight = weight[b]
      best = b
    }
  }
  if (best === -1) return null
  return hueSum[best] / weight[best]
}

export function posterHue(posterPath: string | null | undefined): Promise<number | null> {
  if (!posterPath) return Promise.resolve(null)
  const url = sampleUrl(posterPath)
  let cached = hueCache.get(url)
  if (!cached) {
    cached = new Promise<number | null>((resolve) => {
      const img = new Image()
      img.crossOrigin = 'anonymous'
      img.onload = () => {
        try {
          resolve(dominantHue(img))
        } catch {
          resolve(null) // tainted canvas or similar — keep the defaults
        }
      }
      img.onerror = () => resolve(null)
      img.src = url
    })
    hueCache.set(url, cached)
  }
  return cached
}

export function glowVarsForHue(hue: number): CSSProperties {
  const h = Math.round(((hue % 360) + 360) % 360)
  return {
    '--g1': `hsl(${h} 68% 46%)`,
    '--g2': `hsl(${(h + 48) % 360} 62% 42%)`,
    '--g3': `hsl(${(h + 300) % 360} 60% 46%)`,
  } as CSSProperties
}

// Returns inline CSS variables for the three glow colours once the
// poster has been sampled, or undefined (house defaults) until then
// and whenever sampling isn't possible.
export function usePosterGlow(posterPath: string | null | undefined): CSSProperties | undefined {
  const [vars, setVars] = useState<CSSProperties | undefined>(undefined)
  useEffect(() => {
    let cancelled = false
    setVars(undefined)
    posterHue(posterPath).then((hue) => {
      if (!cancelled && hue != null) setVars(glowVarsForHue(hue))
    })
    return () => {
      cancelled = true
    }
  }, [posterPath])
  return vars
}
