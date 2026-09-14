import { useEffect, useState } from 'react'

// Settings' nav shell is the first place behavior (not just styling)
// genuinely differs by breakpoint — mobile starts at a section list,
// desktop always shows a section's panel — so it needs to know the
// breakpoint in JS, not just in CSS.
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mql = window.matchMedia(query)
    const listener = () => setMatches(mql.matches)
    listener()
    mql.addEventListener('change', listener)
    return () => mql.removeEventListener('change', listener)
  }, [query])
  return matches
}
