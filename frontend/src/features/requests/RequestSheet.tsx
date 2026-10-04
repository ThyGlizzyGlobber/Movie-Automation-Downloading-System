import { useState } from 'react'
import { Link } from 'react-router-dom'
import { cancelRequest } from '../../api/requests'
import Icon from '../../components/ui/Icon'
import Img from '../../components/ui/Img'
import Modal from '../../components/ui/Modal'
import StatusPill from '../../components/ui/StatusPill'
import DownloadBar from '../../components/media/DownloadBar'
import { posterUrl } from '../../lib/tmdbImage'
import { relativeTime } from '../../lib/format'
import { CANCELLABLE, FAILED_STATES, NON_TERMINAL, statusDetail, statusMeta } from '../../lib/status'
import { dominantStatus, groupProgress, onPlexTally, packScopeLabel, requestLabelAndHref, type DisplayItem, type SeasonGroup } from '../../lib/requestGrouping'
import { errorText, useToast } from '../../lib/toast'
import type { RequestOut } from '../../types/requests'
import '../detail/RequestModal.css'

// Cancel while something is still in flight; delete the file once it
// has finished. Both go through the same cancel route. The grid puts
// this one tap from a poster, so cancelling asks first as well.
function useCancel(row: RequestOut, onChanged: () => void) {
  const [busy, setBusy] = useState(false)
  const { toast } = useToast()
  const isDelete = row.status === 'complete'
  async function run(label: string) {
    if (!confirm(isDelete ? `Delete ${label} from Plex?` : `Cancel ${label}?`)) return
    setBusy(true)
    try {
      await cancelRequest(row.id)
      onChanged()
    } catch (err) {
      toast({ tone: 'error', title: `Couldn't ${isDelete ? 'delete' : 'cancel'} that`, body: errorText(err) })
      setBusy(false)
    }
  }
  return { busy, isDelete, run }
}

function CancelButton({ row, onChanged }: { row: RequestOut; onChanged: () => void }) {
  const { busy, isDelete, run } = useCancel(row, onChanged)
  if (!CANCELLABLE.has(row.status)) return null
  return (
    <button className="btn sm danger" disabled={busy} onClick={() => run(row.title)}>
      <Icon name={isDelete ? 'trash' : 'close'} />
      {busy ? (isDelete ? 'Deleting…' : 'Cancelling…') : isDelete ? 'Delete from Plex' : 'Cancel download'}
    </button>
  )
}

// The × on an episode pill. Finished episodes keep theirs off: deleting
// one file of a season is a job for the show page, not a stray tap here.
function EpisodeCancel({ row, label, onChanged }: { row: RequestOut; label: string; onChanged: () => void }) {
  const { busy, isDelete, run } = useCancel(row, onChanged)
  if (!CANCELLABLE.has(row.status) || isDelete) return null
  return (
    <button className="rq-ep-x" aria-label={`Cancel ${label}`} disabled={busy} onClick={() => run(`${row.title} ${label}`)}>
      <Icon name="close" />
    </button>
  )
}

function episodePillLabel(r: RequestOut): string {
  if (r.media_type === 'episode') return `E${String(r.episode_number).padStart(2, '0')}`
  return r.season_number == null ? 'Whole series' : 'Season pack'
}

// A season: a heading with its count and a strip of pills, one per
// episode (or pack), coloured by state. A downloading pill fills from
// the left as it goes instead of carrying an icon.
function SeasonStrip({ season, onChanged }: { season: SeasonGroup; onChanged: () => void }) {
  const rows = season.rows.slice().sort((a, b) => {
    if (a.media_type !== b.media_type) return a.media_type === 'pack' ? -1 : 1
    return (a.episode_number ?? 0) - (b.episode_number ?? 0)
  })
  const { ready, total } = onPlexTally(rows)
  return (
    <div className="rq-season">
      <div className="rq-season-head">
        <b>{season.label}</b>
        <small>
          {ready} of {total} on Plex
        </small>
      </div>
      <div className="rq-eps">
        {rows.map((r) => {
          const meta = statusMeta(r.status)
          const label = episodePillLabel(r)
          const downloading = r.status === 'downloading'
          const pct = downloading && r.download_progress != null ? Math.round(r.download_progress * 100) : null
          return (
            <span
              key={r.id}
              className={`rq-ep ${meta.cls}`}
              style={pct != null ? { ['--fill' as string]: `${pct}%` } : undefined}
              title={`${label} | ${meta.label} | ${statusDetail(r.status, r.download_progress, r.error_message)}`}
            >
              {!downloading && <Icon name={meta.icon} />}
              {label}
              {pct != null && <em>{pct}%</em>}
              <EpisodeCancel row={r} label={label} onChanged={onChanged} />
            </span>
          )
        })}
      </div>
    </div>
  )
}

function movieSub(r: RequestOut): string {
  const bits: string[] = []
  if (r.media_type === 'movie') {
    if (r.release_year) bits.push(String(r.release_year))
    bits.push('Movie')
  } else if (r.media_type === 'episode') {
    bits.push(`S${String(r.season_number).padStart(2, '0')}E${String(r.episode_number).padStart(2, '0')}`)
  } else {
    bits.push(packScopeLabel(r))
  }
  if (r.redownload_mode) bits.push(r.redownload_mode === 'overwrite' ? 'Replacing' : 'Upgrading')
  if (r.requested_by_username) bits.push(`Requested by ${r.requested_by_username}`)
  return bits.join(' | ')
}

// The sheet behind a card's ⋯: everything the grid leaves out — who
// asked, when, the full reason something stopped, a show's seasons
// episode by episode, and the cancel/delete actions.
export default function RequestSheet({ item, onClose, onChanged }: { item: DisplayItem; onClose: () => void; onChanged: () => void }) {
  const rows = item.type === 'standalone' ? [item.row] : item.rows
  const status = item.type === 'standalone' ? item.row.status : dominantStatus(item.rows)
  const updated = rows.reduce((best, r) => (r.updated_at > best ? r.updated_at : best), rows[0].updated_at)
  const title = item.type === 'standalone' ? item.row.title : item.title
  const poster = item.type === 'standalone' ? item.row.poster_path : item.posterPath
  const href = item.type === 'standalone' ? requestLabelAndHref(item.row).href : `#/tv/${item.tmdbId}`

  let sub: string
  let detail: string
  let progress: number | null = null
  if (item.type === 'standalone') {
    const r = item.row
    sub = movieSub(r)
    detail = r.status === 'downloading' ? 'Downloading now' : statusDetail(r.status, r.download_progress, r.error_message)
    if (r.status === 'downloading') progress = r.download_progress
  } else {
    const { ready, total } = onPlexTally(item.rows)
    const active = item.rows.filter((r) => NON_TERMINAL.has(r.status)).length
    const people = [...new Set(item.rows.map((r) => r.requested_by_username).filter(Boolean))]
    sub = [
      'Series',
      `${ready} of ${total} on Plex`,
      active ? `${active} on the way` : null,
      people.length ? `Requested by ${people.join(', ')}` : null,
    ]
      .filter(Boolean)
      .join(' | ')
    // When the show as a whole reads as failed, say why: the newest
    // failed row's own explanation (a pack that stepped aside, a floor
    // miss…).
    const explained = FAILED_STATES.has(status) ? item.rows.filter((r) => FAILED_STATES.has(r.status)).sort((a, b) => b.id - a.id)[0] : null
    detail = explained
      ? statusDetail(explained.status, explained.download_progress, explained.error_message)
      : status === 'downloading'
      ? `${item.rows.filter((r) => r.status === 'downloading').length} of ${item.rows.length} downloading now`
      : statusDetail(status, null)
    if (status === 'downloading') progress = groupProgress(item.rows)
  }

  return (
    <Modal className="request-modal rq-sheet" labelledBy="rq-sheet-title" onClose={onClose}>
      <button className="request-modal-close" aria-label="Close" onClick={onClose}>
        <Icon name="close" />
      </button>
      <div className="request-modal-head">
        <Img className="request-modal-poster" src={posterUrl(poster ?? null)} alt="" />
        <div>
          <h2 id="rq-sheet-title" className="request-modal-title">
            {title}
          </h2>
          <p className="request-modal-sub">{sub}</p>
        </div>
      </div>

      <div className="rq-sheet-status">
        {/* A download is said by its bar, so it gets no pill. */}
        <div className={`rq-sheet-line${status === 'downloading' ? ' live' : ''}`}>
          {status !== 'downloading' && <StatusPill status={status} />}
          <span>{detail}</span>
        </div>
        {status === 'downloading' && <DownloadBar progress={progress} className="rq-sheet-bar" />}
      </div>

      {item.type === 'show' && (
        <div className="rq-seasons">
          {item.seasons.map((season) => (
            <SeasonStrip key={season.key} season={season} onChanged={onChanged} />
          ))}
        </div>
      )}

      <div className="request-modal-foot">
        <span className="request-modal-note">Updated {relativeTime(updated)}</span>
        {item.type === 'standalone' && <CancelButton row={item.row} onChanged={onChanged} />}
        <Link className="btn sm pri" to={href.replace(/^#/, '')} onClick={onClose}>
          {item.type === 'show' ? 'Open show' : 'Open'}
          <Icon name="next" />
        </Link>
      </div>
    </Modal>
  )
}
