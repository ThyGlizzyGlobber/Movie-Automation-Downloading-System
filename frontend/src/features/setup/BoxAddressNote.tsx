import { useQuery } from '@tanstack/react-query'
import { getSetupAddress } from '../../api/setup'

// Under the wizard on a box: what to bookmark. obsidian.local follows the box
// whatever address the router gives it, but some phones can't open .local
// names, so the address itself is offered beside it.
export default function BoxAddressNote({ setupToken }: { setupToken: string }) {
  const query = useQuery({
    queryKey: ['setup-address'],
    queryFn: () => getSetupAddress(setupToken),
    staleTime: 60_000,
    retry: false,
  })
  const addresses = query.data?.addresses ?? []
  const name = addresses.find((a) => a.endsWith('.local'))
  const number = addresses.find((a) => !a.endsWith('.local'))
  if (!name || !number) return null
  return (
    <p className="setup-address-note">
      Bookmark Obsidian at{' '}
      <a className="setup-link" href={name}>
        {name.replace('http://', '')}
      </a>
      . On a phone that can't open it, use{' '}
      <a className="setup-link" href={number}>
        {number.replace('http://', '')}
      </a>
      .
    </p>
  )
}
