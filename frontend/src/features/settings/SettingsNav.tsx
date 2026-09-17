import Icon, { type IconName } from '../../components/Icon'

export interface SettingsSection {
  key: string
  label: string
  icon: IconName
  group: 'Overview' | 'Server' | 'Access' | 'System'
}

// Grouped the way the reference's settings sidebar is (Server / Access /
// System); the panel keys are unchanged.
export const SETTINGS_SECTIONS: SettingsSection[] = [
  { key: 'dashboard', label: 'Dashboard', icon: 'home', group: 'Overview' },
  { key: 'plex', label: 'Plex', icon: 'plex', group: 'Server' },
  { key: 'connections', label: 'Services', icon: 'plug', group: 'Server' },
  { key: 'pipeline', label: 'Downloads', icon: 'sliders', group: 'Server' },
  { key: 'tv', label: 'TV shows', icon: 'clock', group: 'Server' },
  { key: 'storage', label: 'Storage', icon: 'drive', group: 'Server' },
  { key: 'retention', label: 'History', icon: 'trash', group: 'Server' },
  { key: 'household', label: 'Household', icon: 'user', group: 'Access' },
  { key: 'remote-access', label: 'Remote access', icon: 'lock', group: 'Access' },
  { key: 'activity', label: 'Activity', icon: 'chart', group: 'System' },
  { key: 'updates', label: 'Updates', icon: 'refresh', group: 'System' },
  { key: 'about', label: 'About', icon: 'info', group: 'System' },
]

const GROUPS: SettingsSection['group'][] = ['Overview', 'Server', 'Access', 'System']

export default function SettingsNav({ active, onSelect }: { active: string | null; onSelect: (key: string) => void }) {
  return (
    <nav className="settings-nav-list">
      {GROUPS.map((group) => (
        <div className="settings-nav-group" key={group}>
          <div className="settings-nav-group-label">{group}</div>
          {SETTINGS_SECTIONS.filter((s) => s.group === group).map((s) => (
            <button
              key={s.key}
              className={`settings-nav-item${active === s.key ? ' active' : ''}`}
              onClick={() => onSelect(s.key)}
            >
              <Icon name={s.icon} />
              {s.label}
              <Icon name="next" className="settings-nav-item-chevron" />
            </button>
          ))}
        </div>
      ))}
    </nav>
  )
}
