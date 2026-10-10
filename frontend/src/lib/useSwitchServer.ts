import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { switchSessionServer } from '../api/auth'
import { errorText, useToast } from './toast'

// An admin moving between Plex servers. Almost everything on screen is
// that server's — its requests, its shows, its Continue watching, what is
// "On Plex" — so nothing cached from the one being left may show for a
// moment longer than it takes to ask again.
export function useSwitchServer() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [switching, setSwitching] = useState<number | null>(null)

  async function switchTo(serverId: number, name: string) {
    setSwitching(serverId)
    try {
      await switchSessionServer(serverId)
      // Pages not on screen lose what they had, so opening one asks the
      // new server from nothing. What is on screen is asked again in
      // place rather than reset or removed: either blanks it to
      // placeholders, and the page jumps — Settings scrolls back to the
      // top, and losing the session reads as signed out and restarts the
      // app. The feeds this browser keeps between visits are rewritten
      // from the cache as it changes (queryPersistence.ts), so they
      // follow without being cleared here. The toast waits for the
      // answers, so it lands once the page is the new server's.
      queryClient.removeQueries({ type: 'inactive' })
      await queryClient.invalidateQueries()
      toast({ tone: 'ok', title: `Now viewing ${name}` })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't switch servers", body: errorText(err) })
    } finally {
      setSwitching(null)
    }
  }

  return { switchTo, switching }
}
