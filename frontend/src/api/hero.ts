import { request } from './client'
import type { HeroSlide } from '../types/hero'

// Which landing page's carousel is asking. The server picks the slides
// (trending, backdrop-having, most popular first) so all three pages
// agree on what a hero is.
export type HeroKind = 'home' | 'movies' | 'tv'

interface HeroSlideResponse extends Omit<HeroSlide, 'mediaType'> {
  media_type: 'movie' | 'tv'
}

export async function getHeroSlides(kind: HeroKind): Promise<HeroSlide[]> {
  const slides = await request<HeroSlideResponse[]>(`/api/hero?kind=${kind}`)
  // The API speaks snake_case like the rest of them; the carousel has
  // always read `mediaType`. Renamed here rather than at every use.
  return slides.map(({ media_type, ...rest }) => ({ ...rest, mediaType: media_type }))
}
