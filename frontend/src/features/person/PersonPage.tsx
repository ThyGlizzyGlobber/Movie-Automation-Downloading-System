import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getPerson } from '../../api/movies'
import PosterCard from '../../components/PosterCard'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'

// A cast member's own page — their filmography, reusing the same
// PosterCard/.grid every other mixed movie/TV list already uses, since
// /api/person/{id} tags each credit with its own media_type.
export default function PersonPage() {
  const { id } = useParams()
  const personId = Number(id)
  useSetHasHero(false)

  const personQuery = useQuery({ queryKey: ['person', personId], queryFn: () => getPerson(personId) })
  const person = personQuery.data
  usePageTitle(person ? person.name || null : null)

  if (personQuery.isLoading) return <LoadingState />
  if (personQuery.isError || !person) {
    return <ErrorState message={personQuery.error instanceof Error ? personQuery.error.message : undefined} retryHref="#/home" />
  }

  const count = person.credits.length

  return (
    <>
      <h1 className="page-title">{person.name || 'Unknown'}</h1>
      <div className="page-subtitle">
        {count} result{count === 1 ? '' : 's'} with {person.name || 'this person'}
      </div>
      {count ? (
        <div className="grid person-grid">
          {person.credits.map((c) => (
            <PosterCard key={`${c.media_type}-${c.id}`} item={c} mediaType={c.media_type} />
          ))}
        </div>
      ) : (
        <EmptyState message="No filmography found." />
      )}
    </>
  )
}
