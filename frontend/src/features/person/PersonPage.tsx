import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getPerson } from '../../api/movies'
import PosterCard from '../../components/PosterCard'
import Img from '../../components/Img'
import { PosterCardSkeleton, Skel, SkelText } from '../../components/Skeleton'
import ErrorState from '../../components/ErrorState'
import EmptyState from '../../components/EmptyState'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import { profileUrl } from '../../lib/tmdbImage'
import './PersonPage.css'

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

  if (personQuery.isLoading) {
    return (
      <div aria-busy="true">
        <div className="person-head" aria-hidden="true">
          <Skel className="person-photo" />
          <div>
            <h1 className="page-title">
              <SkelText width="6.5em" />
            </h1>
            <div className="person-head-meta">
              <SkelText width="9em" />
            </div>
          </div>
        </div>
        <div className="grid person-grid">
          {Array.from({ length: 21 }, (_, i) => (
            <PosterCardSkeleton key={i} />
          ))}
        </div>
      </div>
    )
  }
  if (personQuery.isError || !person) {
    return <ErrorState message={personQuery.error instanceof Error ? personQuery.error.message : undefined} retryHref="#/home" />
  }

  const count = person.credits.length
  const role = person.known_for_department === 'Acting' ? 'Actor' : person.known_for_department || null
  const meta = [role, `${count} title${count === 1 ? '' : 's'}`].filter(Boolean).join(' · ')

  return (
    <>
      <div className="person-head">
        <Img className="person-photo" src={profileUrl(person.profile_path)} alt="" />
        <div>
          <h1 className="page-title">{person.name || 'Unknown'}</h1>
          <div className="person-head-meta">{meta}</div>
        </div>
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
