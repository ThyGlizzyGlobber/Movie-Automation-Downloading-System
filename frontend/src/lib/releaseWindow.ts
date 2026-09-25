import { FALLBACK_REGION } from './regions'
import type { ReleaseDatesResult } from '../types/movies'

// TMDB's /movie/{id}/release_dates `type` field — the same numbers the
// backend reads in tmdb.py, kept in both places because each side asks a
// different question of them: the backend asks "is this out yet", this
// asks "when will it be".
const DIGITAL = 4
const PHYSICAL = 5
const THEATRICAL = [2, 3]

// How long a wide cinema release actually takes to reach digital.
// Measured 2026-09-26 against TMDB itself — the 39 most popular films
// released Jan 2025 to Jul 2026 with both a US theatrical and a US
// digital date on file: median 46 days, mean 48, quartiles 32 and 56,
// full range 1 to 158.
//
// That spread is why an estimate is rendered as a month and never as a
// day: the middle half of releases land across a three-and-a-half week
// window, which routinely straddles a month boundary, and a specific
// date would be claiming a precision the data does not have.
const TYPICAL_WINDOW_DAYS = 46

export interface ReleaseOutlook {
  /** How much TMDB actually knows, so the UI can be as sure as the data
   *  is and no surer. `digital` is a dated digital release on file;
   *  `physical` is a disc street date standing in for one, which in
   *  practice lands the same week or later; `estimate` is derived from
   *  the cinema date and is a guess; `unknown` means the only thing we
   *  know is that it isn't out. */
  kind: 'digital' | 'physical' | 'estimate' | 'unknown'
  /** ISO yyyy-mm-dd. Null when `kind` is `unknown`. */
  date: string | null
  /** Ready to render: "14 Nov 2026", or "Nov 2026" for an estimate
   *  (which carries no day — see TYPICAL_WINDOW_DAYS), or "Not
   *  announced yet". */
  when: string
  /** "32 Days" — null once the date is today or past, which happens
   *  while TMDB still hasn't caught up. */
  eta: string | null
}

function dated(entries: ReleaseDatesResult[], types: number[], after: Date): string[] {
  return entries
    .flatMap((r) => r.release_dates)
    .filter((d) => d.type != null && types.includes(d.type) && d.release_date)
    .map((d) => d.release_date!.slice(0, 10))
    .filter((iso) => new Date(`${iso}T00:00:00`) > after)
    .sort()
}

/**
 * The soonest date of these types on file: the household's own region
 * first, then the US, then anywhere at all.
 *
 * The cascade runs per kind of date rather than per region, and that
 * ordering is the whole point. TMDB logs digital street dates
 * thoroughly for the US and patchily everywhere else, while it logs
 * *cinema* dates for everyone — so a household outside the US has an
 * entry for its own region on almost every title, and a region-first
 * cascade that stopped at the first region with any entry at all would
 * find the local cinema date, declare the digital one unknown, and
 * never look at the US record that actually had it.
 *
 * Falling back past the household's region is also the right answer on
 * the merits here, not just a coverage workaround: a release reaching
 * the indexers is a worldwide event, whatever a territory's own
 * paperwork says about it.
 */
function soonest(results: ReleaseDatesResult[], region: string, types: number[], after: Date): string | undefined {
  for (const scope of [
    results.filter((r) => r.iso_3166_1 === region),
    results.filter((r) => r.iso_3166_1 === FALLBACK_REGION),
    results,
  ]) {
    const found = dated(scope, types, after)[0]
    if (found) return found
  }
  return undefined
}

function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })
}

// Month and year only, in the same short-month style as the exact
// dates beside it — the absent day is what marks it as approximate, so
// it doesn't need a hedging word in front of it as well.
function formatMonth(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString([], { month: 'short', year: 'numeric' })
}

// A plain day count, at every distance. It used to round into weeks
// and months past a fortnight, which reads more naturally in a sentence
// but is the wrong unit beside a date: "in 3 weeks" next to 17 Oct
// makes you do the arithmetic to find out whether that means the 17th,
// and the rounding meant it sometimes didn't.
function etaFrom(iso: string, now: Date): string | null {
  const days = Math.ceil((new Date(`${iso}T00:00:00`).getTime() - now.getTime()) / 86400000)
  if (days <= 0) return null
  return `${days} Day${days === 1 ? '' : 's'}`
}

/**
 * When a movie that isn't out yet should become downloadable.
 *
 * The cinema date is the one everybody knows and the one nobody wants:
 * a title is in cinemas for weeks before a copy worth having exists,
 * and the gap between the two is exactly the thing this answers.
 *
 * Returns something for every coming-soon title, including the ones
 * TMDB has nothing on — "not announced yet" is a real answer to "when
 * can I get this", and better than a blank space that reads like a
 * loading state.
 */
export function releaseOutlook(
  movie: { release_date?: string | null; release_dates?: { results?: ReleaseDatesResult[] } },
  region: string = FALLBACK_REGION,
  now: Date = new Date(),
): ReleaseOutlook {
  const results = movie.release_dates?.results ?? []

  const digital = soonest(results, region, [DIGITAL], now)
  if (digital) return { kind: 'digital', date: digital, when: formatDate(digital), eta: etaFrom(digital, now) }

  const physical = soonest(results, region, [PHYSICAL], now)
  if (physical) return { kind: 'physical', date: physical, when: formatDate(physical), eta: etaFrom(physical, now) }

  // Nothing on file for the drop itself, so work forward from the cinema
  // date — its own if TMDB has one, otherwise the headline release_date,
  // which for an unreleased title is the cinema date under another name.
  const cinema = soonest(results, region, THEATRICAL, new Date(0)) ?? movie.release_date?.slice(0, 10)
  if (cinema) {
    const guess = new Date(`${cinema}T00:00:00`)
    guess.setDate(guess.getDate() + TYPICAL_WINDOW_DAYS)
    // A film that opened months ago and still has no digital record on
    // file is one TMDB has lost track of, not one arriving imminently.
    // Saying "around last July" would be worse than admitting we don't
    // know.
    if (guess > now) {
      const iso = guess.toISOString().slice(0, 10)
      return { kind: 'estimate', date: iso, when: formatMonth(iso), eta: null }
    }
  }

  return { kind: 'unknown', date: null, when: 'Not announced yet', eta: null }
}

// What kind of date this is, in one capitalised word. Parallel on
// purpose: three dates that mean three different things read as three
// variants of one label rather than three separate inventions, and the
// reader learns the scheme once. Digital is named rather than left bare
// because it is only the default in our heads — on the page, an
// unlabelled date beside a "(Disc)" one just looks like the label went
// missing.
const KIND_LABEL: Record<ReleaseOutlook['kind'], string> = {
  digital: 'Digital',
  physical: 'Disc',
  estimate: 'Estimate',
  unknown: '',
}

/** The tile on a coming-soon movie's page: the date, what kind of date
 *  it is, and how long the wait is, in one line. */
export function downloadableLabel(outlook: ReleaseOutlook): string {
  const kind = KIND_LABEL[outlook.kind]
  // No date to qualify or count down to — the phrase is the whole answer.
  if (!kind) return outlook.when
  // The countdown trails the pair rather than splitting it, so the
  // qualifier stays attached to the date it qualifies. An estimate has
  // no eta by construction: a countdown to a month we guessed would be
  // the one part of this that sounded certain.
  return `${outlook.when} (${kind})${outlook.eta ? ` | ${outlook.eta}` : ''}`
}
