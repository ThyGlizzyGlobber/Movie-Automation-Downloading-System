import { useLayoutEffect, useRef, useState, type ImgHTMLAttributes } from 'react'

// An <img> that shows the loading shimmer in its own box until the
// picture arrives, then fades in. `plain` skips the shimmer for images
// with transparent parts (title logos) or no box of their own (the
// blurred backdrop); those only fade.
//
// A picture the browser already has — Back to Home, a poster tapped on
// to reach its page — is shown at once, with neither: it is complete
// before the first paint, and running the shimmer and the fade anyway
// is what kept anything from ever looking instant.
export default function Img({ className, plain = false, onLoad, onError, ...props }: ImgHTMLAttributes<HTMLImageElement> & { plain?: boolean }) {
  const ref = useRef<HTMLImageElement>(null)
  const [settled, setSettled] = useState<string | undefined>(undefined)
  const [cached, setCached] = useState<string | undefined>(undefined)
  useLayoutEffect(() => {
    const img = ref.current
    if (img?.complete && img.naturalWidth > 0) {
      setSettled(props.src)
      setCached(props.src)
    }
  }, [props.src])
  const done = settled === props.src
  const state = done ? (cached === props.src ? ' img-ready' : ' img-in') : plain ? '' : ' img-wait'
  return (
    <img
      decoding="async"
      {...props}
      ref={ref}
      className={`${className ? `${className} ` : ''}img${state}`}
      onLoad={(e) => {
        setSettled(props.src)
        onLoad?.(e)
      }}
      onError={(e) => {
        setSettled(props.src)
        onError?.(e)
      }}
    />
  )
}
