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

// Offered as the default audio track language. Mirrors the backend's
// AUDIO_LANGUAGE_ALIASES (language.py) — a code missing from that table
// would match no track in any container and silently do nothing.
export const AUDIO_LANGUAGES: { code: string; name: string }[] = [
  { code: 'en', name: 'English' },
  { code: 'ar', name: 'Arabic' },
  { code: 'zh', name: 'Chinese' },
  { code: 'da', name: 'Danish' },
  { code: 'nl', name: 'Dutch' },
  { code: 'fi', name: 'Finnish' },
  { code: 'fr', name: 'French' },
  { code: 'de', name: 'German' },
  { code: 'hi', name: 'Hindi' },
  { code: 'it', name: 'Italian' },
  { code: 'ja', name: 'Japanese' },
  { code: 'ko', name: 'Korean' },
  { code: 'no', name: 'Norwegian' },
  { code: 'pl', name: 'Polish' },
  { code: 'pt', name: 'Portuguese' },
  { code: 'ru', name: 'Russian' },
  { code: 'es', name: 'Spanish' },
  { code: 'sv', name: 'Swedish' },
  { code: 'tr', name: 'Turkish' },
]

// A representative IANA zone per region, for converting a release date
// out of the country that published it and into the household's own
// calendar — see releaseWindow.localiseDate.
//
// The US is Los Angeles rather than New York deliberately: the
// conversion asks when the source country's day is *over*, and the US
// day ends on the west coast.
export const REGION_TIMEZONE: Record<string, string> = {
  AU: 'Australia/Sydney',
  BR: 'America/Sao_Paulo',
  CA: 'America/Toronto',
  DE: 'Europe/Berlin',
  ES: 'Europe/Madrid',
  FR: 'Europe/Paris',
  GB: 'Europe/London',
  IE: 'Europe/Dublin',
  IN: 'Asia/Kolkata',
  IT: 'Europe/Rome',
  JP: 'Asia/Tokyo',
  MX: 'America/Mexico_City',
  NL: 'Europe/Amsterdam',
  NZ: 'Pacific/Auckland',
  SE: 'Europe/Stockholm',
  US: 'America/Los_Angeles',
  ZA: 'Africa/Johannesburg',
}
