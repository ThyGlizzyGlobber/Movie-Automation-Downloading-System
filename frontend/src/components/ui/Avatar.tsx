import { useEffect, useState } from 'react'
import Icon from './Icon'
import { initialsOf } from '../../lib/format'
import './Avatar.css'

// The Plex account picture; initials (or the user icon) when there is no
// picture or it fails to load. One stable URL the browser revalidates
// (the server answers 304 while it is unchanged, and asks plex.tv at most
// once a minute), so a change made on plex.tv shows on the next load. It
// used to be re-requested under a new URL every minute, which downloaded
// the whole picture — up to ~700KB — on every app open.
export default function Avatar({ src, name, hasPicture, className }: { src: string; name: string | null | undefined; hasPicture: boolean; className?: string }) {
  const [broken, setBroken] = useState(false)
  useEffect(() => setBroken(false), [src, hasPicture])
  const initials = initialsOf(name)
  if (hasPicture && !broken) {
    return <img className={`avatar-img${className ? ` ${className}` : ''}`} src={src} alt="" decoding="async" onError={() => setBroken(true)} />
  }
  if (initials) return <span className={`avatar-initials${className ? ` ${className}` : ''}`}>{initials}</span>
  return <Icon name="user" className={className} />
}
