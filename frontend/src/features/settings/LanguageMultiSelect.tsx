import './LanguageMultiSelect.css'

// Fixed list rather than free text — a release's language tag is matched
// literally (whole-word) against its filename, so picking from known
// real-world tags beats typing them out by hand every time.
const LANGUAGE_OPTIONS = [
  'English',
  'Spanish',
  'French',
  'German',
  'Italian',
  'Portuguese',
  'Dutch',
  'Russian',
  'Polish',
  'Swedish',
  'Norwegian',
  'Danish',
  'Finnish',
  'Turkish',
  'Arabic',
  'Hindi',
  'Japanese',
  'Korean',
  'Chinese',
  'Multi',
  'Dual Audio',
]

function summaryText(selected: string[]): string {
  if (selected.length === 0) return 'Any'
  if (selected.length <= 2) return selected.join(', ')
  return `${selected.length} selected`
}

export default function LanguageMultiSelect({
  id,
  selected,
  onChange,
}: {
  id: string
  selected: string[]
  onChange: (next: string[]) => void
}) {
  const selectedLower = new Set(selected.map((s) => s.toLowerCase()))

  function toggle(opt: string) {
    const isSelected = selectedLower.has(opt.toLowerCase())
    onChange(isSelected ? selected.filter((s) => s.toLowerCase() !== opt.toLowerCase()) : [...selected, opt])
  }

  return (
    <details className="lang-select" id={id}>
      <summary className="lang-select-summary">{summaryText(selected)}</summary>
      <div className="lang-select-panel">
        {LANGUAGE_OPTIONS.map((opt) => (
          <label className="lang-select-option" key={opt}>
            <input type="checkbox" checked={selectedLower.has(opt.toLowerCase())} onChange={() => toggle(opt)} />
            {opt}
          </label>
        ))}
      </div>
    </details>
  )
}
