import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getNotificationPrefs, sendTestNotification, setNotificationPrefs } from '../../api/notifications'
import { currentPushSubscription, disablePushOnThisDevice, enablePushOnThisDevice, pushSupported } from '../../lib/push'
import { errorText, useToast } from '../../lib/toast'
import SettingRow from '../../components/SettingRow'
import Toggle from '../../components/Toggle'
import LoadingState from '../../components/LoadingState'

// Settings › Notifications: what to be told about, and whether this
// device gets a push even when Meridian is closed.
export default function NotificationsPanel() {
  const queryClient = useQueryClient()
  const prefs = useQuery({ queryKey: ['notification-prefs'], queryFn: getNotificationPrefs })
  const { toast } = useToast()
  const [thisDevice, setThisDevice] = useState<'unknown' | 'on' | 'off' | 'unsupported'>('unknown')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!pushSupported()) {
      setThisDevice('unsupported')
      return
    }
    currentPushSubscription().then((sub) => setThisDevice(sub ? 'on' : 'off'))
  }, [])

  if (prefs.isLoading || !prefs.data) return <LoadingState />
  const p = prefs.data

  async function save(patch: Partial<{ notify_own: boolean; notify_household: boolean }>) {
    try {
      await setNotificationPrefs({ notify_own: p.notify_own, notify_household: p.notify_household, ...patch })
      queryClient.invalidateQueries({ queryKey: ['notification-prefs'] })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't save that", body: errorText(err) })
    }
  }

  async function togglePush(on: boolean) {
    setBusy(true)
    try {
      if (on) {
        const result = await enablePushOnThisDevice()
        if (result === 'on') {
          setThisDevice('on')
          toast({ tone: 'ok', title: 'Push is on for this device' })
        } else if (result === 'denied') {
          toast({ tone: 'error', title: 'Notifications are blocked', body: 'Allow them for this site in your browser settings.' })
        } else {
          toast({ tone: 'error', title: 'Push is not available here', body: 'It needs HTTPS and a browser that supports it.' })
        }
      } else {
        await disablePushOnThisDevice()
        setThisDevice('off')
      }
      queryClient.invalidateQueries({ queryKey: ['notification-prefs'] })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't change push", body: errorText(err) })
    } finally {
      setBusy(false)
    }
  }

  async function sendTest() {
    try {
      const { pushed } = await sendTestNotification()
      queryClient.invalidateQueries({ queryKey: ['notifications'] })
      toast({ tone: 'ok', title: 'Test sent', body: pushed ? `Pushed to ${pushed} device${pushed === 1 ? '' : 's'}.` : 'Check the bell.' })
    } catch (err) {
      toast({ tone: 'error', title: "Couldn't send a test", body: errorText(err) })
    }
  }

  return (
    <div className="settings-panel-card">
      <h2>Notifications</h2>
      <p className="settings-sub">Hear when a request lands in Plex or needs a hand.</p>
      <SettingRow label="My requests" hint="Tell me when something I asked for is ready or failed.">
        <Toggle checked={p.notify_own} onChange={(v) => save({ notify_own: v })} label="Notify me about my requests" />
      </SettingRow>
      <SettingRow label="The household's requests" hint="Also tell me when anyone else's request lands.">
        <Toggle checked={p.notify_household} onChange={(v) => save({ notify_household: v })} label="Notify me about household requests" />
      </SettingRow>
      <SettingRow
        label="Push to this device"
        hint={
          thisDevice === 'unsupported'
            ? 'Not available in this browser. Open Meridian over HTTPS or add it to your home screen.'
            : !p.push_available
              ? 'Not set up on the server.'
              : `Get a notification even when Meridian is closed. ${p.devices} device${p.devices === 1 ? '' : 's'} signed up.`
        }
      >
        <Toggle
          checked={thisDevice === 'on'}
          onChange={(v) => {
            if (!busy && thisDevice !== 'unsupported' && p.push_available) togglePush(v)
          }}
          label="Push to this device"
        />
      </SettingRow>
      <SettingRow label="Send a test" hint="Puts one in the bell, and pushes to any signed-up device.">
        <button className="settings-btn secondary sm" onClick={sendTest}>
          Send test
        </button>
      </SettingRow>
    </div>
  )
}
