"""Resolves one selected TMDB show into a show identity and per-episode
search queries — the Stage 1 equivalent for TV. Family disambiguates
*which* show by tapping a poster; episode-level matching (Stage 10) is a
separate problem, same split as movies' resolve.py/score.py."""

from dataclasses import dataclass, field

from app.normalize import generate_variants
from app.tmdb import TMDBClient

# S01E04-style, the dominant modern scene/indexer convention. Alternate
# formats (1x04, "Season 1 Episode 4") are a named, documented gap for v1 —
# same style as Stage 2's cam-tag gap — not solved here.
EPISODE_TOKEN_FORMAT = "S{season:02d}E{episode:02d}"


@dataclass
class ShowIdentity:
    tmdb_id: int
    title: str
    original_title: str
    variants: list[str] = field(default_factory=list)


def episode_query(title_variant: str, season: int, episode: int) -> str:
    return f"{title_variant} {EPISODE_TOKEN_FORMAT.format(season=season, episode=episode)}"


def resolve_show(tmdb_id: int, client: TMDBClient) -> ShowIdentity:
    show = client.get_tv(tmdb_id)

    title = show.get("name") or show.get("original_name") or ""
    original_title = show.get("original_name") or title

    # No release-year variant: unlike a movie, a show has no single release
    # year an episode's search query would benefit from (the episode token
    # itself is the tighter signal — see Stage 10's pass-one design).
    variants = generate_variants(title, original_title, release_year=None)

    return ShowIdentity(
        tmdb_id=tmdb_id,
        title=title,
        original_title=original_title,
        variants=variants,
    )
