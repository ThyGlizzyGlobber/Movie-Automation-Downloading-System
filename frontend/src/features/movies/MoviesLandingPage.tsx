import DiscoverLanding, { type DiscoverSource } from '../discover/DiscoverLanding'
import { getDiscoverTrending } from '../../api/movies'

// Its genre and service rows are defined with the rest of the page's rows
// in backend/app/api/recommendations.py (MOVIE_GENRES, ROW_PROVIDERS).
const MOVIES: DiscoverSource = {
  page: 'movies',
  mediaType: 'movie',
  title: 'Movies',
  trending: getDiscoverTrending,
}

export default function MoviesLandingPage() {
  return <DiscoverLanding source={MOVIES} />
}
