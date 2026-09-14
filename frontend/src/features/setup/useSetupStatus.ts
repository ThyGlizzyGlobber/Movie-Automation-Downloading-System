import { useQuery } from '@tanstack/react-query'
import { getSetupStatus } from '../../api/setup'

export function useSetupStatus() {
  return useQuery({ queryKey: ['setupStatus'], queryFn: getSetupStatus })
}
