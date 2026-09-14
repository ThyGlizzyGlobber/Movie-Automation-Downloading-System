import { useParams } from 'react-router-dom'
import CategoryPage from './CategoryPage'
import type { TmdbListResponse } from '../types/movies'

export default function GenreCategoryPage({
  mediaType,
  fetchByGenre,
}: {
  mediaType: 'movie' | 'tv'
  fetchByGenre: (genreId: number, page: number) => Promise<TmdbListResponse>
}) {
  const { id, name } = useParams()
  const genreId = Number(id)
  return (
    <CategoryPage
      title={name ? decodeURIComponent(name) : 'Genre'}
      mediaType={mediaType}
      fetchPage={(page) => fetchByGenre(genreId, page)}
      queryKey={[mediaType, 'genre', genreId]}
    />
  )
}
