export interface SettingsSection {
  key: string
  label: string
  icon: string
}

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { key: 'pipeline', label: 'Pipeline & Quality', icon: 'tune' },
  { key: 'tv', label: 'TV Schedule', icon: 'schedule' },
  { key: 'retention', label: 'Retention', icon: 'auto_delete' },
  { key: 'connections', label: 'Connections', icon: 'cable' },
  { key: 'plex', label: 'Plex Server', icon: 'dns' },
  { key: 'activity', label: 'Activity Dashboard', icon: 'insights' },
  { key: 'updates', label: 'Updates', icon: 'system_update' },
]

export default function SettingsNav({ active, onSelect }: { active: string | null; onSelect: (key: string) => void }) {
  return (
    <nav className="settings-nav-list">
      {SETTINGS_SECTIONS.map((s) => (
        <button
          key={s.key}
          className={`settings-nav-item${active === s.key ? ' active' : ''}`}
          onClick={() => onSelect(s.key)}
        >
          <span className="material-symbols-rounded">{s.icon}</span>
          {s.label}
          <span className="material-symbols-rounded settings-nav-item-chevron">chevron_right</span>
        </button>
      ))}
    </nav>
  )
}
