import { useMemo, useState } from 'react'
import MediaRow from './MediaRow'
import PosterCard from './PosterCard'
import { PosterCardSkeleton } from './Skeleton'
import type { TmdbListItem } from '../types/movies'
import { rankMoves, type RankMove } from '../lib/topTen'
import { browseHref } from '../api/browse'
import './TopTenRow.css'

const TOP_N = 10

function MoveChip({ move }: { move: RankMove }) {
  if (move.kind === 'same') return null
  if (move.kind === 'new') return <span className="top10-move new">New</span>
  return (
    <span className={`top10-move ${move.kind}`}>
      {move.kind === 'up' ? '▲' : '▼'} {move.by}
    </span>
  )
}

// The Top 10 row from the Obsidian reference: a large glass-gradient
// numeral behind each of the ten most popular titles, with a Movies/TV
// switch in the header. Numerals are drawn as text with the gradient
// clipped to the glyph so they read as light through glass rather than
// as solid digits.
export default function TopTenRow({
  movies,
  shows,
  loading = false,
  only,
}: {
  movies: TmdbListItem[]
  shows: TmdbListItem[]
  loading?: boolean
  /* Pages about one half of the catalogue pass it here, which drops the
     Movies/TV toggle. Without this the Movies page would carry a TV tab
     that opens an empty row — a control whose only function is to
     disappoint. */
  only?: 'movie' | 'tv'
}) {
  const [picked, setMediaType] = useState<'movie' | 'tv'>('movie')
  const mediaType = only ?? picked
  const items = useMemo(() => (mediaType === 'movie' ? movies : shows).slice(0, TOP_N), [mediaType, movies, shows])
  const moves = useMemo(
    () =>
      rankMoves(
        mediaType,
        items.map((it) => it.id),
      ),
    [mediaType, items],
  )
  if (!loading && !items.length) return null

  return (
    <MediaRow
      className="top10-row"
      title="Top 10"
      qualifier="this week"
      items={items}
      mediaType={mediaType}
      loading={loading}
      skeletonCount={TOP_N}
      renderSkeleton={(index) => (
        <div className="top10-card">
          <div className="top10-num">{index + 1}</div>
          <div className="top10-poster">
            <PosterCardSkeleton caption={false} />
          </div>
        </div>
      )}
      expandHref={browseHref({ type: mediaType, sort: 'trending' })}
      headerRight={
        only ? undefined : (
        <div className="seg" role="tablist" aria-label="Top 10 type">
          <button role="tab" aria-selected={mediaType === 'movie'} className={mediaType === 'movie' ? 'active' : ''} onClick={() => setMediaType('movie')}>
            Movies
          </button>
          <button role="tab" aria-selected={mediaType === 'tv'} className={mediaType === 'tv' ? 'active' : ''} onClick={() => setMediaType('tv')}>
            TV
          </button>
        </div>
        )
      }
      renderItem={(item, index) => (
        <div className="top10-card">
          <div className="top10-num" aria-hidden="true">
            {index + 1}
          </div>
          <div className="top10-poster">
            <span className="top10-rank-sr">Number {index + 1}</span>
            <PosterCard item={item} mediaType={mediaType} caption={false} />
            <MoveChip move={moves[index]} />
          </div>
        </div>
      )}
    />
  )
}
