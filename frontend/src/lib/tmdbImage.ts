const IMG_BASE = 'https://image.tmdb.org/t/p/'
const POSTER_SIZE = 'w342'
// w1280 (not w780) — the hero/detail-page backdrop is now a full-bleed
// banner that can be 2000px+ wide on a real desktop window since main's
// max-width cap was removed; w780 stretched via background-size:cover
// looked visibly soft at that size. TMDB's own next size up is
// "original" (often 1920px+, sometimes much larger) — w1280 is the
// sharpest fixed size below that, without the bandwidth cost of
// "original" on a household NAS connection.
const BACKDROP_SIZE = 'w1280'
const PROFILE_SIZE = 'w185'
// Tiny poster used only for colour sampling (lib/palette.ts) — see the
// note there on why it must differ from POSTER_SIZE.
const SAMPLE_SIZE = 'w92'

const PLACEHOLDER_POSTER =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300">' +
      '<rect width="100%" height="100%" fill="#201e1c"/></svg>',
  )

export function posterUrl(path: string | null | undefined): string {
  return path ? IMG_BASE + POSTER_SIZE + path : PLACEHOLDER_POSTER
}

export function backdropUrl(path: string | null | undefined, size: string = BACKDROP_SIZE): string {
  return path ? IMG_BASE + size + path : ''
}

export function profileUrl(path: string | null | undefined): string {
  return path ? IMG_BASE + PROFILE_SIZE + path : PLACEHOLDER_POSTER
}

const STILL_SIZE = 'w300'

export function stillUrl(path: string | null | undefined): string {
  return path ? IMG_BASE + STILL_SIZE + path : ''
}

export function sampleUrl(path: string | null | undefined): string {
  return path ? IMG_BASE + SAMPLE_SIZE + path : ''
}

// Title logos: transparent PNGs, served at 500px wide which is plenty
// for a hero at 2x.
export function logoUrl(path: string | null | undefined, size: 'w500' | 'original' = 'w500'): string | null {
  return path ? `https://image.tmdb.org/t/p/${size}${path}` : null
}
