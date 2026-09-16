import { useEffect, useState } from 'react'
import { useMediaQuery } from '../../lib/hooks'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import SettingsNav, { SETTINGS_SECTIONS } from './SettingsNav'
import DashboardPanel from './DashboardPanel'
import PipelinePanel from './PipelinePanel'
import TvSchedulePanel from './TvSchedulePanel'
import RetentionPanel from './RetentionPanel'
import ConnectionsPanel from './ConnectionsPanel'
import PlexServerPanel from './PlexServerPanel'
import RemoteAccessPanel from './RemoteAccessPanel'
import ActivityDashboardPanel from './ActivityDashboardPanel'
import UpdatesPanel from './UpdatesPanel'
import StoragePanel from './StoragePanel'
import NotificationsPanel from './NotificationsPanel'
import HouseholdPanel from './HouseholdPanel'
import AboutPanel from './AboutPanel'
import './SettingsPage.css'
import Icon from '../../components/Icon'

function renderPanel(key: string, jump: (key: string) => void) {
  switch (key) {
    case 'dashboard':
      return <DashboardPanel onOpen={jump} />
    case 'pipeline':
      return <PipelinePanel />
    case 'tv':
      return <TvSchedulePanel />
    case 'retention':
      return <RetentionPanel />
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

function sectionFromHash(): string | null {
  const hash = window.location.hash.split('#')[2]
  const key = hash?.startsWith('settings-') ? hash.slice('settings-'.length) : null
  return key && SETTINGS_SECTIONS.some((s) => s.key === key) ? key : null
}

// Desktop: a sticky sidebar and one section at a time, opening on the
// dashboard, no page title. Phones: the section list, drilling into one section.
export default function SettingsPage() {
  usePageTitle('Settings')
  useSetHasHero(false)
  const isDesktop = useMediaQuery('(min-width: 860px)')
  const [section, setSection] = useState<string | null>(() => sectionFromHash())

  useEffect(() => {
    function onHash() {
      const key = sectionFromHash()
      if (key) setSection(key)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  function jump(key: string) {
    setSection(key)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const current = isDesktop ? (section ?? 'dashboard') : section

  if (isDesktop) {
    return (
      <div className="settings-page">
        <div className="settings-shell">
          <div className="settings-nav-col">
            <SettingsNav active={current} onSelect={jump} />
          </div>
          <div className="settings-panel-col">
            <section className="settings-section" key={current}>
              {renderPanel(current!, jump)}
            </section>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="settings-shell">
      <div className={`settings-nav-col${section ? ' settings-hide-mobile' : ''}`}>
        <SettingsNav active={section} onSelect={jump} />
      </div>
      <div className={`settings-panel-col${!section ? ' settings-hide-mobile' : ''}`}>
        {section && (
          <>
            <button className="settings-back-btn" onClick={() => setSection(null)}>
              <Icon name="back" />
              Settings
            </button>
            {renderPanel(section, jump)}
          </>
        )}
      </div>
    </div>
  )
}
