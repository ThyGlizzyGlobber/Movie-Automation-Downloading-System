// Stands in for pages not built yet (Parts A7-A10 of the migration plan)
// so navigation/chrome has somewhere real to point at in the meantime.
export default function ComingSoon({ label }: { label: string }) {
  return <div style={{ padding: '24px 4px', color: 'var(--text-dim)' }}>{label} — coming soon.</div>
}
