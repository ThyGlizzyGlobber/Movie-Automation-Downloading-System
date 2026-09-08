"""Resolves one selected TMDB show into a show identity and per-episode
search queries — the Stage 1 equivalent for TV. Family disambiguates
*which* show by tapping a poster; episode-level matching (Stage 10) is a
separate problem, same split as movies' resolve.py/score.py."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

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
    # Deliberately unused by matching (Stage 9's episode-token signal is
    # tighter than any year check) — carried only for Stage 11's Plex
    # folder-naming convention, "<Show> (<year>) {tmdb-<id>}", which wants
    # a year purely as a display/disambiguation hint. `None` when TMDB
    # hasn't got a first_air_date yet (an unreleased show).
    first_air_year: int | None = None


def episode_query(title_variant: str, season: int, episode: int) -> str:
    return f"{title_variant} {EPISODE_TOKEN_FORMAT.format(season=season, episode=episode)}"


def season_pack_queries(title_variant: str, season: int) -> list[str]:
    """Stage 13: two query shapes tried for one season-pack search, mirroring
    real indexer conventions for a whole-season release ("Show Season 01" vs
    "Show S01 COMPLETE") — a plain S01-only query would also match every
    single-episode release for the season, which is exactly what the
    per-episode pipeline already handles; a pack search wants a query that
    at least *suggests* the pack shape, even though the real filtering still
    happens in pack_score.py's pass-one gate, not here."""
    return [f"{title_variant} Season {season:02d}", f"{title_variant} S{season:02d} COMPLETE"]


def series_pack_query(title_variant: str) -> str:
    return f"{title_variant} complete series"


def season_range_pack_queries(title_variant: str, start: int, end: int) -> list[str]:
    """Stage 14.x: query shapes for a release bundling seasons `start`
    through `end` inclusive (`start < end`) — e.g. a "Reacher S01-S03"
    torrent covering every season aired so far while a later season is
    still airing. Mirrors `season_pack_queries`'s "try a couple of
    real-world naming conventions" approach, extended to a range; the
    real filtering happens in `pack_score.py`'s pass-one gate, same as
    every other pack query here."""
    return [f"{title_variant} S{start:02d}-S{end:02d}", f"{title_variant} Seasons {start}-{end}"]


def _cutoff_date(now: datetime | None, buffer_hours: float) -> str:
    """The latest air_date treated as "already aired" — `buffer_hours`
    shifted back from `now` (default: the actual current time) before
    taking the date, so a same-day air_date doesn't count as aired until
    that many hours have passed since midnight on it. TMDB only ever
    gives a date, not a release time, and a real-world case (Ted Lasso
    S04E06, air_date 2026-09-08, 2026-09-08) found this app's own
    recheck cycle searching for an episode the *instant* the calendar
    date rolled over — hours before the show's actual release time, and
    long before any real torrent could plausibly exist yet, which is
    exactly the window fake/malicious releases get uploaded into to
    catch automated tools searching too early. `buffer_hours=0` (the
    exact-date behavior this replaced) disables the delay entirely."""
    now = now or datetime.now()
    return (now - timedelta(hours=buffer_hours)).date().isoformat()


def aired_episode_numbers(episodes: list[dict], now: datetime | None = None, buffer_hours: float = 0.0) -> list[int]:
    """Episode numbers from a TMDB season's episode list that have already
    aired (a known air_date at or before `_cutoff_date`) — shared by
    worker.py's check_show() (Stage 12) and pipeline.py's download_pack()
    (Stage 13), which both need to turn a raw TMDB season listing into
    "which episodes should actually exist by now". See `_cutoff_date` for
    what `buffer_hours` does and why it exists."""
    cutoff = _cutoff_date(now, buffer_hours)
    return [
        ep["episode_number"]
        for ep in episodes
        if ep.get("episode_number") is not None and ep.get("air_date") and ep["air_date"] <= cutoff
    ]


def season_is_complete(episodes: list[dict], now: datetime | None = None, buffer_hours: float = 0.0) -> bool:
    """True once every episode TMDB knows about for this season already
    has an air_date at or before `_cutoff_date` — i.e. the season has
    finished its run, not just "some episodes have aired so far". False
    for a season still actively releasing new episodes (an unaired or
    entirely unscheduled entry still remains), or one with no episodes
    listed at all (nothing to judge either way). See `_cutoff_date` for
    what `buffer_hours` does and why it exists.

    Used by worker.py's check_show() (Stage 14.x) to decide whether a
    season's still-unhandled aired episodes should be requested as a
    single season pack instead of individual per-episode searches — an
    older, fully-aired season is realistically far more likely to still
    have a well-seeded pack release than well-seeded individual episode
    releases, which tend to go cold once a show has moved on."""
    if not episodes:
        return False
    cutoff = _cutoff_date(now, buffer_hours)
    return all(ep.get("air_date") and ep["air_date"] <= cutoff for ep in episodes)


def resolve_show(tmdb_id: int, client: TMDBClient) -> ShowIdentity:
    show = client.get_tv(tmdb_id)

    title = show.get("name") or show.get("original_name") or ""
    original_title = show.get("original_name") or title
    first_air_date = show.get("first_air_date") or ""
    first_air_year = int(first_air_date[:4]) if first_air_date[:4].isdigit() else None

    # No release-year variant: unlike a movie, a show has no single release
    # year an episode's search query would benefit from (the episode token
    # itself is the tighter signal — see Stage 10's pass-one design).
    variants = generate_variants(title, original_title, release_year=None)

    return ShowIdentity(
        tmdb_id=tmdb_id,
        title=title,
        original_title=original_title,
        variants=variants,
        first_air_year=first_air_year,
    )
