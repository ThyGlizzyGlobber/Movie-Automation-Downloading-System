"""Resolves one selected TMDB title into a media identity and an ordered
list of qBittorrent query variants. Family disambiguates *which* movie by
tapping a poster; this only ever disambiguates *which release* of that
already-specific movie (Stage 2's job)."""

from dataclasses import dataclass, field

from app.normalize import normalize_text
from app.tmdb import TMDBClient

_SUBTITLE_SEPARATORS = (":", " - ")
MAX_VARIANTS = 4


@dataclass
class MediaIdentity:
    tmdb_id: int
    title: str
    original_title: str
    release_year: int | None
    variants: list[str] = field(default_factory=list)


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


def resolve(tmdb_id: int, client: TMDBClient) -> MediaIdentity:
    movie = client.get_movie(tmdb_id)

    title = movie.get("title") or movie.get("original_title") or ""
    original_title = movie.get("original_title") or title
    release_date = movie.get("release_date") or ""
    release_year = int(release_date[:4]) if release_date[:4].isdigit() else None

    variants = generate_variants(title, original_title, release_year)

    return MediaIdentity(
        tmdb_id=tmdb_id,
        title=title,
        original_title=original_title,
        release_year=release_year,
        variants=variants,
    )
