import type { TmdbListItem } from '../types/movies'

export type SearchMediaType = 'movie' | 'tv'

// How many of each side's results the guess looks at. TMDB already
// returns them in relevance order, so the title a search is "really"
// about is at the front; a handful is enough to survive one odd item
// being ranked first, without letting something unrelated further down
// the page decide the tab.
const CONSIDERED = 5

function bestVoteCount(items: TmdbListItem[]): number {
  return Math.max(0, ...items.slice(0, CONSIDERED).map((i) => i.vote_count ?? 0))
}

function bestPopularity(items: TmdbListItem[]): number {
  return Math.max(0, ...items.slice(0, CONSIDERED).map((i) => i.popularity ?? 0))
}

// Which tab a search should land on: whichever side the query is better
// known as.
//
// Decided on vote_count, not TMDB's popularity score. Popularity is a
// rolling, recency-weighted number that is not comparable between the
// movie and TV endpoints — measured against live TMDB, TV routinely
// scores an order of magnitude higher for the same word ("supernatural"
// 322 vs 2, "the office" 253 vs 2), which biases every tie toward TV.
// On "batman" it is worse than biased, it is arbitrary: 64.4 for movies
// against 62.1 for TV, and the movie side's 64.4 belongs to "Batman:
// Knightfall Part 1" with 352 votes — an obscure title, a hair's
// breadth, and a metric that reshuffles daily. vote_count is a plain
// count of ratings, directly comparable across both, and it separates
// the same two cases decisively: batman 23,197 to 1,900 for movies,
// "law and order" 725 to 24 for TV.
//
// Popularity is still the tiebreak, for a title too new to have votes
// on either side.
export function pickBestMediaType(movies: TmdbListItem[], shows: TmdbListItem[]): SearchMediaType {
  if (!movies.length) return shows.length ? 'tv' : 'movie'
  if (!shows.length) return 'movie'

  const movieVotes = bestVoteCount(movies)
  const showVotes = bestVoteCount(shows)
  if (movieVotes !== showVotes) return showVotes > movieVotes ? 'tv' : 'movie'

  return bestPopularity(shows) > bestPopularity(movies) ? 'tv' : 'movie'
}
