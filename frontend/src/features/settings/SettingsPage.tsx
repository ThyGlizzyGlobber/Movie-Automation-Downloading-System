import { useState } from 'react'
import { useMediaQuery } from '../../lib/hooks'
import { usePageTitle, useSetHasHero } from '../../lib/chrome'
import SettingsNav, { SETTINGS_SECTIONS } from './SettingsNav'
import PipelinePanel from './PipelinePanel'
import TvSchedulePanel from './TvSchedulePanel'
import RetentionPanel from './RetentionPanel'
import ConnectionsPanel from './ConnectionsPanel'
import PlexServerPanel from './PlexServerPanel'
import ActivityDashboardPanel from './ActivityDashboardPanel'
import UpdatesPanel from './UpdatesPanel'
import './SettingsPage.css'

function renderPanel(key: string) {
  switch (key) {
    case 'pipeline':
      return <PipelinePanel />
    case 'tv':
      return <TvSchedulePanel />
    case 'retention':
      return <RetentionPanel />
    case 'connections':
      return <ConnectionsPanel />
    case 'plex':
      return <PlexServerPanel />
    case 'activity':
      return <ActivityDashboardPanel />
    case 'updates':
      return <UpdatesPanel />
    default:
      return null
  }
}

// Part I's redesign: a left-hand nav + active panel on desktop/tablet;
// on mobile, a section list that drills into its own full screen with a
// back action (Part H) — driven here by whether a section is genuinely
// selected, not just squeezed CSS, since the two breakpoints need
// different *behavior*, not just different layout.
export default function SettingsPage() {
  usePageTitle('Settings')
  useSetHasHero(false)
  const isDesktop = useMediaQuery('(min-width: 860px)')
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const effectiveSection = activeSection ?? (isDesktop ? SETTINGS_SECTIONS[0].key : null)

  return (
    <div className="settings-shell">
      <div className={`settings-nav-col${effectiveSection ? ' settings-hide-mobile' : ''}`}>
        <SettingsNav active={effectiveSection} onSelect={setActiveSection} />
      </div>
      <div className={`settings-panel-col${!effectiveSection ? ' settings-hide-mobile' : ''}`}>
        {effectiveSection && (
          <>
            <button className="settings-back-btn" onClick={() => setActiveSection(null)}>
              <span className="material-symbols-rounded">chevron_left</span>
              Settings
            </button>
            {renderPanel(effectiveSection)}
          </>
        )}
      </div>
    </div>
  )
}
