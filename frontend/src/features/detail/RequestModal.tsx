import { useQuery } from '@tanstack/react-query'
import { getStorage } from '../../api/system'
import { formatBytes } from '../../lib/format'
import { posterUrl } from '../../lib/tmdbImage'
import Icon from '../../components/ui/Icon'
import Modal from '../../components/ui/Modal'
import './RequestModal.css'

// The request sheet: what's being asked for, what the household will get
// (always the best copy that exists — no quality to pick), how much room
// the library has, and one button.
export default function RequestModal({
  open,
  title,
  subtitle,
  posterPath,
  submitLabel = 'Add to Plex',
  onClose,
  onSubmit,
}: {
  open: boolean
  title: string
  subtitle?: string
  posterPath?: string | null
  submitLabel?: string
  onClose: () => void
  onSubmit: () => void
}) {
  const storage = useQuery({ queryKey: ['storage'], queryFn: getStorage, enabled: open, staleTime: 60_000 })
  if (!open) return null
  const free = storage.data?.available ? storage.data.free_bytes : null

  return (
    <Modal className="request-modal" labelledBy="request-modal-title" onClose={onClose}>
      <button className="request-modal-close" aria-label="Close" onClick={onClose}>
        <Icon name="close" />
      </button>
      <div className="request-modal-head">
        {posterPath !== undefined && <img className="request-modal-poster" src={posterUrl(posterPath)} alt="" />}
        <div>
          <h2 id="request-modal-title" className="request-modal-title">
            {title}
          </h2>
          {subtitle && <p className="request-modal-sub">{subtitle}</p>}
        </div>
      </div>
      <div className="request-modal-best">
        <Icon name="hd" />
        <div>
          <b>The best copy out there</b>
          <small>4K when it exists, otherwise the sharpest release within the household's download limits.</small>
        </div>
      </div>
      <div className="request-modal-foot">
        <span className="request-modal-note">{free != null ? `${formatBytes(free)} free.` : 'Free space unknown.'}</span>
        <button className="request-modal-cancel" onClick={onClose}>
          Cancel
        </button>
        <button className="request-modal-submit" onClick={onSubmit}>
          <Icon name="plus" />
          {submitLabel}
        </button>
      </div>
    </Modal>
  )
}
