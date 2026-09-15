import { useEffect, useState } from 'react'
import Icon from './Icon'
import { initialsOf } from '../lib/useActiveRequestCount'

// The Plex account picture, re-requested every minute so a change made
// on plex.tv shows up here without a sign-out; initials (or the user
// icon) when there is no picture or it fails to load.
export default function Avatar({ src, name, hasPicture, className }: { src: string; name: string | null | undefined; hasPicture: boolean; className?: string }) {
  const [bucket, setBucket] = useState(() => Math.floor(Date.now() / 60_000))
  const [broken, setBroken] = useState(false)
  useEffect(() => {
    const t = window.setInterval(() => setBucket(Math.floor(Date.now() / 60_000)), 60_000)
    return () => window.clearInterval(t)
  }, [])
  useEffect(() => setBroken(false), [src, hasPicture])
  const initials = initialsOf(name)
  if (hasPicture && !broken) {
    return <img className={`avatar-img${className ? ` ${className}` : ''}`} src={`${src}?v=${bucket}`} alt="" onError={() => setBroken(true)} />
  }
  if (initials) return <span className={`avatar-initials${className ? ` ${className}` : ''}`}>{initials}</span>
  return <Icon name="user" className={className} />
}
