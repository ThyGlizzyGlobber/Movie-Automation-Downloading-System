import type { IconName } from '../components/Icon'

// The four sections, shared by the top bar, the tab bar and the start-up
// skeleton.
export const SECTIONS: { tab: string; label: string; href: string; icon: IconName }[] = [
  { tab: 'home', label: 'Home', href: '/home', icon: 'home' },
  { tab: 'movies', label: 'Movies', href: '/movies', icon: 'film' },
  { tab: 'tv', label: 'TV Shows', href: '/tv', icon: 'tv' },
  { tab: 'requests', label: 'Requests', href: '/requests', icon: 'download' },
]
