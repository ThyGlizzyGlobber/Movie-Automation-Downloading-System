import { useLayoutEffect, useRef, useState } from 'react'

// The "More"/"Less" expand toggle shared by the hero overview and the
// detail panel's repeated copy of it. The button only renders when the
// text is genuinely truncated by the 4-line clamp (scrollHeight >
// clientHeight while still clamped) — a visible "More" that toggles
// nothing on click reads as a broken button.
export default function ClampedText({
  text,
  textClassName,
  buttonClassName,
}: {
  text: string
  textClassName: string
  buttonClassName: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [canExpand, setCanExpand] = useState(false)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setCanExpand(el.scrollHeight > el.clientHeight + 1)
  }, [text])

  return (
    <>
      <div ref={ref} className={`${textClassName} ${expanded ? 'expanded' : 'clamped'}`}>
        {text ? text : <em>No synopsis available.</em>}
      </div>
      {canExpand && (
        <button className={buttonClassName} type="button" onClick={() => setExpanded((e) => !e)}>
          {expanded ? 'Less' : 'More'}
        </button>
      )}
    </>
  )
}
