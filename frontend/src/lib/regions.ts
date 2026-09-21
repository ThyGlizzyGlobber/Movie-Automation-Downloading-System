// Age ratings are per-country and not interchangeable — the same show is
// TV-MA in the US and MA15+ in Australia — so every certification lookup
// takes the household's region rather than a constant. It used to be a
// constant, twice, with different values: 'AU' in homeHero and 'US' in
// detailHelpers, which is why one title could be rated two ways on two
// pages of the same app.
export const FALLBACK_REGION = 'US'

// Offered in Settings. Not exhaustive and not meant to be — TMDB carries
// certifications for a long tail of countries, and a region with nothing
// for a given title falls back rather than showing a blank.
export const CERTIFICATION_REGIONS: { code: string; name: string }[] = [
  { code: 'AU', name: 'Australia' },
  { code: 'BR', name: 'Brazil' },
  { code: 'CA', name: 'Canada' },
  { code: 'DE', name: 'Germany' },
  { code: 'ES', name: 'Spain' },
  { code: 'FR', name: 'France' },
  { code: 'GB', name: 'United Kingdom' },
  { code: 'IE', name: 'Ireland' },
  { code: 'IN', name: 'India' },
  { code: 'IT', name: 'Italy' },
  { code: 'JP', name: 'Japan' },
  { code: 'MX', name: 'Mexico' },
  { code: 'NL', name: 'Netherlands' },
  { code: 'NZ', name: 'New Zealand' },
  { code: 'SE', name: 'Sweden' },
  { code: 'US', name: 'United States' },
  { code: 'ZA', name: 'South Africa' },
]
