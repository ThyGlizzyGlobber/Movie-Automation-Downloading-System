"""Shared token-normalization utility.

One normalizer, many uses: title/year relevance (Stage 1), and resolution,
language, source, codec, container matching against release filenames
(Stage 2). Always whole-token, normalized matching — never substring.
"""

import re
import unicodedata

# [\W_], not [^\w]: \w counts the underscore as a word character, so
# "[^\w]+" left it in place and glued whole tokens together. A real
# release does not agree — PHDTeam's packs name episodes
# "Love, Death & Robots_S03E01_Tri roboti.mkv", which normalized to the
# single token "robots_s03e01_tri" and matched nothing: not the episode
# patterns, and not "1080p" or "x264" either when they are separated the
# same way. Underscore is a separator wherever a release name uses one.
_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, strip diacritics/accents, replace punctuation/symbols
    and underscores with spaces, collapse whitespace. 'Dune: Part Two'
    -> 'dune part two'."""
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


_SEASON_EPISODE_RE = re.compile(r"^s(\d{1,2})e(\d{1,3})$")
_SEASON_ONLY_RE = re.compile(r"^s(\d{1,2})$")
_EPISODE_ONLY_RE = re.compile(r"^e(\d{1,3})$")
# "14x01" — the convention Italian and other European groups use instead
# of "S14E01" (confirmed live: every one of the 20 files in
# Supernatural.S14.ITA.ENG.1080p.AMZN.WEBRip.AAC.x265-Pir8 is named
# Supernatural.14xNN.<titolo>..., and not one of them matched the three
# patterns above). Opt-in per caller, and deliberately narrower than the
# others: the episode half must be at least two digits, which is what
# keeps an aspect-ratio token out of it — "16x9" and "4x3" do not match,
# and a resolution like "1920x1080" fails on the season half's own
# two-digit cap. "16x10" would still read as S16E10, which is the reason
# this is opt-in rather than always on; see extract_episode_identity.
_SEASON_X_EPISODE_RE = re.compile(r"^(\d{1,2})x(\d{2,3})$")


def extract_episode_identity(tokens: list[str], *, allow_x_form: bool = False) -> tuple[int, int] | None:
    """Pulls a concrete (season, episode) pair out of a filename's own
    already-tokenized tokens, if one is present — a contiguous "s01e04"
    token, or adjacent "s01"/"e04" tokens, first match wins; `None` if
    neither shape appears anywhere. Two independent callers: Stage 13's
    `media_organizer.organize_pack` (which episode a pack's individual
    file represents) and score.py's `passes_not_a_tv_episode_filter`
    (whether a *movie* candidate is actually shaped like a TV episode at
    all — same title, wrong kind of thing, confirmed live: a movie
    search for "Mayday" (2026) turned up an episode of the unrelated
    long-running documentary series of the same name, 2026-09-14). Lives
    here rather than in tv_score.py so score.py (movies) can reuse it
    without importing from tv_score.py, which itself imports from
    score.py — this module sits below both, with no dependency on
    either.

    `allow_x_form` adds the "14x01" shape (_SEASON_X_EPISODE_RE), and
    only organize_pack passes it. The two callers want different things
    from an ambiguous token: inside a pack this app downloaded for a
    known show, "14x01" is an episode number and nothing else, so
    reading it is pure gain. On score.py's side a false positive is
    silent and costly in the other direction — that filter *rejects* a
    candidate it believes is a TV episode, so teaching it one more
    pattern risks dropping a legitimate movie whose release name happens
    to carry an "NNxNN" token, a failure nobody would see. Off by
    default, so the movie gate stays exactly as strict as it was."""
    for token in tokens:
        match = _SEASON_EPISODE_RE.match(token)
        if match:
            return int(match.group(1)), int(match.group(2))
    if allow_x_form:
        for token in tokens:
            match = _SEASON_X_EPISODE_RE.match(token)
            if match:
                return int(match.group(1)), int(match.group(2))
    for i in range(len(tokens) - 1):
        season_match = _SEASON_ONLY_RE.match(tokens[i])
        episode_match = _EPISODE_ONLY_RE.match(tokens[i + 1])
        if season_match and episode_match:
            return int(season_match.group(1)), int(episode_match.group(1))
    return None


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
MAX_VARIANTS = 5


def _title_subtitle_split(title: str) -> tuple[str, str] | None:
    """Splits `title` on its first recognized subtitle separator into
    (head, tail), or `None` if no separator is present. Which side is
    actually the searchable, distinctive part of the title depends on
    the specific title and isn't something this can know algorithmically
    — "Dune: Part Two" wants its head ("Dune"), but "Special Ops:
    Lioness" wants its tail ("Lioness"), the actual show name, not the
    franchise-label prefix. Confirmed live (2026-09-14): only ever
    generating the head meant "Lioness" was never tried as a search
    variant at all, and the pipeline settled for a worse release its
    other variants (the full title, or the equally head-only "Special
    Ops" fallback) happened to find, missing a real 2160p release a
    plain "Lioness" search found easily. `generate_variants` now adds
    both sides as separate variant candidates rather than guessing which
    one matters — pipeline.py searches every variant and merges the
    results (never stopping at the first that finds anything), so
    including a weaker variant alongside a stronger one only costs one
    extra search call, never a worse outcome."""
    for sep in _SUBTITLE_SEPARATORS:
        if sep in title:
            head, tail = title.split(sep, 1)
            head, tail = head.strip(), tail.strip()
            if head and tail and head != title:
                return head, tail
    return None


def generate_variants(title: str, original_title: str, release_year: int | None) -> list[str]:
    """Up to MAX_VARIANTS ranked queries: canonical title, original_title
    (if different), title without subtitle, subtitle without title,
    title+year. Deduplicated on normalized form, original ranking order
    preserved."""
    candidates = [title]

    if normalize_text(original_title) != normalize_text(title):
        candidates.append(original_title)

    split = _title_subtitle_split(title)
    if split:
        head, tail = split
        seen_so_far = {normalize_text(c) for c in candidates}
        if normalize_text(head) not in seen_so_far:
            candidates.append(head)
            seen_so_far.add(normalize_text(head))
        if normalize_text(tail) not in seen_so_far:
            candidates.append(tail)

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
