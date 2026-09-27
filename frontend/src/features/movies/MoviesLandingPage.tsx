import DiscoverLanding, { type DiscoverSource } from '../discover/DiscoverLanding'
import { getDiscoverByGenre, getDiscoverByProvider, getDiscoverPopular, getDiscoverTrending, getComingSoon } from '../../api/movies'

const MOVIES: DiscoverSource = {
  page: 'movies',
  mediaType: 'movie',
  title: 'Movies',
  trendingQualifier: 'movies',
  genres: [
    { id: 28, label: 'Action' },
    { id: 35, label: 'Comedies' },
    { id: 27, label: 'Horror' },
    { id: 10749, label: 'Romance' },
    { id: 16, label: 'Animation' },
    { id: 99, label: 'Documentaries' },
  ],
  trending: getDiscoverTrending,
  popular: getDiscoverPopular,
  comingSoon: getComingSoon,
  byGenre: getDiscoverByGenre,
  byProvider: getDiscoverByProvider,
}

export default function MoviesLandingPage() {
  return <DiscoverLanding source={MOVIES} />
}
