import type { CSSProperties, ReactNode } from 'react'
import { Skel, SkelText, SkelWords } from '../../components/Skeleton'

const LABEL_WIDTHS = ['42%', '30%', '52%', '36%', '46%']
const HINT_WIDTHS = ['68%', '54%', '74%', '60%', '48%']

// A settings card while its data loads: the card's real heading and
// sub-line (a placeholder for one built from the data, none for null), then
// setting rows with a label, a hint and a field-sized control.
export function SettingsCardSkeleton({
  title,
  sub,
  subSample,
  rows = 3,
  style,
}: {
  title: ReactNode
  sub?: ReactNode
  /* Sample text for a sub-line that depends on the data. */
  subSample?: string
  rows?: number
  style?: CSSProperties
}) {
  return (
    <div className="settings-panel-card" style={style} aria-busy="true">
      <h2>{title}</h2>
      {sub !== null && <p className="settings-sub">{sub ?? <SkelWords text={subSample ?? 'Loading the details for this section.'} />}</p>}
      {Array.from({ length: rows }, (_, i) => (
        <div className="setting-row" key={i} aria-hidden="true">
          <div className="setting-row-text">
            <b>
              <SkelText width={LABEL_WIDTHS[i % LABEL_WIDTHS.length]} />
            </b>
            <small>
              <SkelText width={HINT_WIDTHS[i % HINT_WIDTHS.length]} />
            </small>
          </div>
          <div className="setting-row-control">
            <Skel className="setting-control-skel" />
          </div>
        </div>
      ))}
    </div>
  )
}
