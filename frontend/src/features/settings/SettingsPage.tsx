import { useEffect, useRef, useState } from 'react'
import { useMediaQuery } from '../../lib/hooks'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import SettingsNav, { SETTINGS_SECTIONS } from './SettingsNav'
import PipelinePanel from './PipelinePanel'
import TvSchedulePanel from './TvSchedulePanel'
import RetentionPanel from './RetentionPanel'
import ConnectionsPanel from './ConnectionsPanel'
import PlexServerPanel from './PlexServerPanel'
import RemoteAccessPanel from './RemoteAccessPanel'
import ActivityDashboardPanel from './ActivityDashboardPanel'
import UpdatesPanel from './UpdatesPanel'
import StoragePanel from './StoragePanel'
import QualityProfilesPanel from './QualityProfilesPanel'
import NotificationsPanel from './NotificationsPanel'
import HouseholdPanel from './HouseholdPanel'
import AboutPanel from './AboutPanel'
import './SettingsPage.css'
import Icon from '../../components/Icon'

function renderPanel(key: string) {
  switch (key) {
    case 'pipeline':
      return <PipelinePanel />
    case 'tv':
      return <TvSchedulePanel />
    case 'retention':
      return <RetentionPanel />
    case 'profiles':
      return <QualityProfilesPanel />
    case 'storage':
      return <StoragePanel />
    case 'connections':
      return <ConnectionsPanel />
    case 'plex':
      return <PlexServerPanel />
    case 'remote-access':
      return <RemoteAccessPanel />
    case 'activity':
      return <ActivityDashboardPanel />
    case 'updates':
      return <UpdatesPanel />
    case 'household':
      return <HouseholdPanel />
    case 'notifications':
      return <NotificationsPanel />
    case 'about':
      return <AboutPanel />
    default:
      return null
  }
}

// Desktop: the reference's one long page of cards with a sticky sidebar
// that follows the scroll and jumps to a card on click. Phones: a
// section list that drills into one card at a time.
export default function SettingsPage() {
  usePageTitle('Settings')
  useSetHasHero(false)
  const isDesktop = useMediaQuery('(min-width: 860px)')
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const [spy, setSpy] = useState<string>(SETTINGS_SECTIONS[0].key)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isDesktop || !listRef.current) return
    const sections = Array.from(listRef.current.querySelectorAll<HTMLElement>('[data-section]'))
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setSpy((visible[0].target as HTMLElement).dataset.section!)
      },
      { rootMargin: '-120px 0px -60% 0px', threshold: 0 },
    )
    sections.forEach((s) => observer.observe(s))
    return () => observer.disconnect()
  }, [isDesktop])

  useEffect(() => {
    const hash = window.location.hash.split('#')[2]
    if (hash?.startsWith('settings-')) jump(hash.slice('settings-'.length))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDesktop])

  function jump(key: string) {
    if (!isDesktop) {
      setActiveSection(key)
      return
    }
    setSpy(key)
    listRef.current?.querySelector<HTMLElement>(`[data-section="${key}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  if (isDesktop) {
    return (
      <div className="settings-shell">
        <div className="settings-nav-col">
          <SettingsNav active={spy} onSelect={jump} />
        </div>
        <div className="settings-panel-col" ref={listRef}>
          <div className="settings-heading">
            <h1 className="settings-title">Settings</h1>
            <p className="settings-lead">Plex, downloads, storage and who can get in</p>
          </div>
          {SETTINGS_SECTIONS.map((s) => (
            <section key={s.key} className="settings-section" data-section={s.key} id={`settings-${s.key}`}>
              {renderPanel(s.key)}
            </section>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="settings-shell">
      <div className={`settings-nav-col${activeSection ? ' settings-hide-mobile' : ''}`}>
        <SettingsNav active={activeSection} onSelect={jump} />
      </div>
      <div className={`settings-panel-col${!activeSection ? ' settings-hide-mobile' : ''}`}>
        {activeSection && (
          <>
            <button className="settings-back-btn" onClick={() => setActiveSection(null)}>
              <Icon name="back" />
              Settings
            </button>
            {renderPanel(activeSection)}
          </>
        )}
      </div>
    </div>
  )
}
