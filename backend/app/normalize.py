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
