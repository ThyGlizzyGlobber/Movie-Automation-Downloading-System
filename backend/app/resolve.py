"""Resolves one selected TMDB title into a media identity and an ordered
list of qBittorrent query variants. Family disambiguates *which* movie by
tapping a poster; this only ever disambiguates *which release* of that
already-specific movie (Stage 2's job)."""

from dataclasses import dataclass, field

from app.normalize import generate_variants
from app.tmdb import TMDBClient


@dataclass
class MediaIdentity:
    tmdb_id: int
    title: str
    original_title: str
    release_year: int | None
    variants: list[str] = field(default_factory=list)


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
