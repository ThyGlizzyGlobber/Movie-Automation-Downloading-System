import { useQuery } from '@tanstack/react-query'
import { getRecentlyAdded } from '../api/plex'
import { plexWebUrl } from '../lib/format'
import MediaRow from './MediaRow'
import PosterCard from './PosterCard'

// "New in your library": Plex's Recently Added, one card per title, on
// Home. Cards open the detail page when Plex knows the TMDB id.
export default function RecentlyAddedRow() {
  const query = useQuery({ queryKey: ['plex-recently-added'], queryFn: getRecentlyAdded, staleTime: 120_000 })
  const data = query.data
  if (query.isLoading) return <MediaRow title="New" qualifier="in your library" items={[]} mediaType="movie" loading />
  if (!data || !data.available || !data.items.length) return null
  const items = data.items.map((it) => ({ ...it, id: Number(it.rating_key) }))
  return (
    <MediaRow
      title="New"
      qualifier="in your library"
      items={items}
      mediaType={(it) => it.media_type}
      renderItem={(item) => (
        <PosterCard
          item={{ id: item.tmdb_id ?? item.id, title: item.title, release_date: item.year ? `${item.year}-01-01` : null }}
          mediaType={item.media_type}
          mixed
          posterSrc={item.poster_url}
          href={item.tmdb_id ? (item.media_type === 'tv' ? `#/tv/${item.tmdb_id}` : `#/movies/${item.tmdb_id}`) : plexWebUrl(null, item.rating_key)}
        />
      )}
    />
  )
}
