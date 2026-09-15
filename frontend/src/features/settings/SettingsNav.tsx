import Icon, { type IconName } from '../../components/Icon'

export interface SettingsSection {
  key: string
  label: string
  icon: IconName
  group: 'Server' | 'Access' | 'System'
}

// Grouped the way the reference's settings sidebar is (Server / Access /
// System); the panel keys are unchanged.
export const SETTINGS_SECTIONS: SettingsSection[] = [
  { key: 'plex', label: 'Plex Server', icon: 'plex', group: 'Server' },
  { key: 'connections', label: 'Connections', icon: 'plug', group: 'Server' },
  { key: 'pipeline', label: 'Pipeline & Quality', icon: 'sliders', group: 'Server' },
  { key: 'tv', label: 'TV Schedule', icon: 'clock', group: 'Server' },
  { key: 'retention', label: 'Retention', icon: 'trash', group: 'Server' },
  { key: 'remote-access', label: 'Remote Access', icon: 'lock', group: 'Access' },
  { key: 'activity', label: 'Activity Dashboard', icon: 'chart', group: 'System' },
  { key: 'updates', label: 'Updates', icon: 'refresh', group: 'System' },
]

const GROUPS: SettingsSection['group'][] = ['Server', 'Access', 'System']

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
