"""Shared token-normalization utility.

One normalizer, many uses: title/year relevance (Stage 1), and resolution,
language, source, codec, container matching against release filenames
(Stage 2). Always whole-token, normalized matching — never substring.
"""

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, strip diacritics/accents, replace punctuation/symbols with
    spaces, collapse whitespace. 'Dune: Part Two' -> 'dune part two'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    no_punct = _PUNCT_RE.sub(" ", stripped)
    return _WS_RE.sub(" ", no_punct).strip().lower()


def tokenize(text: str) -> list[str]:
    """Normalize then split into whole tokens."""
    normalized = normalize_text(text)
    return normalized.split(" ") if normalized else []


def has_token(text: str, token: str) -> bool:
    """Whole-token match: does `token` appear as a complete token in `text`,
    after normalizing both sides? Never a substring match — '265' must not
    match inside '1265', and '4k' must not match inside '4kb'."""
    return normalize_text(token) in tokenize(text)


def token_overlap(a: str, b: str) -> set[str]:
    """Set of normalized tokens shared between two strings, ignoring order
    and duplicates. Used for title-relevance gating."""
    return set(tokenize(a)) & set(tokenize(b))


def titles_match(a: str, b: str) -> bool:
    """True if two *already-normalized* (`normalize_text`-passed) titles
    are the same, or one is a whole-word prefix/suffix of the other.
    Exists for Plex library matching: a real, recurring mismatch is one
    source (typically Plex's own scraped/matched title) carrying a
    franchise prefix or subtitle TMDB's own title doesn't have — "Star
    Wars: The Mandalorian and Grogu" in Plex vs. TMDB's plain "The
    Mandalorian and Grogu" — which an exact-string match misses entirely.
    Requires the shorter side to have at least one word (an empty string
    never matches anything, including another empty string via this
    path — that's the `a == b` branch's job). At the user's explicit
    request, this deliberately allows a single-word match too (e.g. a
    one-word title/alias on one side) — a real, accepted false-positive
    risk for a generic one-word title that happens to be a prefix/suffix
    of an unrelated longer one, traded for never missing a real match."""
    if a == b:
        return True
    words_a, words_b = a.split(), b.split()
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    if len(shorter) < 1:
        return False
    return longer[: len(shorter)] == shorter or longer[-len(shorter) :] == shorter


# -- Title-variant generation. Shared by resolve.py (movies) and
#    tv_resolve.py (shows) — a title's subtitle/original-title/year
#    shape doesn't depend on what kind of thing it is. --

_SUBTITLE_SEPARATORS = (":", " - ")
MAX_VARIANTS = 4


def _title_without_subtitle(title: str) -> str | None:
    for sep in _SUBTITLE_SEPARATORS:
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if head and head != title:
                return head
    return None


def generate_variants(title: str, original_title: str, release_year: int | None) -> list[str]:
    """Up to MAX_VARIANTS ranked queries: canonical title, original_title
    (if different), title without subtitle, title+year. Deduplicated on
    normalized form, original ranking order preserved."""
    candidates = [title]

    if normalize_text(original_title) != normalize_text(title):
        candidates.append(original_title)

    subtitle_free = _title_without_subtitle(title)
    if subtitle_free and normalize_text(subtitle_free) not in {normalize_text(c) for c in candidates}:
        candidates.append(subtitle_free)

    if release_year:
        candidates.append(f"{title} {release_year}")

    seen: set[str] = set()
    variants: list[str] = []
    for candidate in candidates:
        key = normalize_text(candidate)
        if key and key not in seen:
            seen.add(key)
            variants.append(candidate)
        if len(variants) == MAX_VARIANTS:
            break

    return variants
