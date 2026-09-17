import { useState, type ImgHTMLAttributes } from 'react'

// An <img> that shows the loading shimmer in its own box until the
// picture arrives, then fades in. `plain` skips the shimmer for images
// with transparent parts (title logos) or no box of their own (the
// blurred backdrop); those only fade.
export default function Img({ className, plain = false, onLoad, onError, ...props }: ImgHTMLAttributes<HTMLImageElement> & { plain?: boolean }) {
  const [settled, setSettled] = useState<string | undefined>(undefined)
  const done = settled === props.src
  const state = done ? ' img-in' : plain ? '' : ' img-wait'
  return (
    <img
      {...props}
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
