"""Resolves one selected TMDB show into a show identity and per-episode
search queries — the Stage 1 equivalent for TV. Family disambiguates
*which* show by tapping a poster; episode-level matching (Stage 10) is a
separate problem, same split as movies' resolve.py/score.py."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.normalize import generate_variants
from app.tmdb import TMDBClient

logger = logging.getLogger("app.tv_resolve")

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
    # How many seasons TMDB knows of — lets the series-pack gate accept a
    # release labelled as a season range ("S01-S03", "Complete Seasons 1
    # to 3") when that range covers the whole show. None when unknown.
    number_of_seasons: int | None = None
    # How many of those seasons have finished airing (None when TMDB gave
    # no season list). While the last season is still airing, a pack
    # spanning every *finished* season is the best "whole series" there
    # is, and the series-pack gate accepts it.
    finished_seasons: int | None = None
    # TMDB says the show is over (Ended / Canceled): nothing new to follow.
    ended: bool = False
    # Deliberately unused by matching (Stage 9's episode-token signal is
    # tighter than any year check) — carried only for Stage 11's Plex
    # folder-naming convention, "<Show> (<year>) {tmdb-<id>}", which wants
    # a year purely as a display/disambiguation hint. `None` when TMDB
    # hasn't got a first_air_date yet (an unreleased show).
    first_air_year: int | None = None
    # Frontend migration Part J1 — see MediaIdentity's own comment; same
    # idea, carried through to every episode/pack request row this show
    # identity backs.
    poster_path: str | None = None
    # The ids TVmaze can be reached through, for episode release *times*
    # (tvmaze.py) — TMDB only carries a date. None when TMDB has no
    # external_ids for the show, which is the fallback path.
    tvdb_id: int | None = None
    imdb_id: str | None = None


def episode_query(title_variant: str, season: int, episode: int) -> str:
    return f"{title_variant} {EPISODE_TOKEN_FORMAT.format(season=season, episode=episode)}"


def season_pack_queries(title_variant: str, season: int) -> list[str]:
    """Four query shapes tried for one season-pack search. Originally just
    two ("Show Season 01" / "Show S01 COMPLETE"), deliberately avoiding a
    bare "S01"-only query since that also matches every single-episode
    release for the season. Confirmed live (2026-09-14) that restraint
    actively missed a real, correctly-labeled 1080p season pack for The
    Mentalist S01: qBittorrent's search plugins don't do lenient full-text
    matching the way a human browsing a tracker's own search box does —
    neither of the two original literal query strings surfaced a torrent
    named "...Season 1 S01 (1080p BluRay...)" at all, even though a plain
    "the mentalist season 1" search found it immediately.

    The single-episode-release concern the original comment raised is a
    non-issue in practice: pack_score.py's `passes_season_pack_gate`
    already rejects anything carrying an episode token regardless of which
    query surfaced it, so a broader, noisier net here only costs one more
    search call (pipeline.py now searches every query shape and merges
    results anyway — see `_search_pack_queries`), never a wrong result."""
    return [
        f"{title_variant} Season {season:02d}",
        f"{title_variant} S{season:02d} COMPLETE",
        f"{title_variant} Season {season}",
        f"{title_variant} S{season:02d}",
        # Bare title, for the same reason `series_pack_queries` has one.
        title_variant,
    ]


def series_pack_query(title_variant: str) -> str:
    return f"{title_variant} complete series"


def series_pack_queries(title_variant: str) -> list[str]:
    """Query shapes for a whole-series search. qBittorrent's plugins match
    the literal query text, so "<show> complete series" only ever
    surfaces releases with those exact adjacent words — confirmed live
    (2026-09-16, Batman: The Brave and the Bold): that query returned two
    dead torrents, while the real, well-seeded packs were named "Complete
    Seasons 1 to 3", "S01-S03" and plain "- Complete". A bare title
    search returns everything and lets pack_score's gate decide, which
    is what a person does by hand."""
    return [series_pack_query(title_variant), f"{title_variant} complete", title_variant]


def season_range_pack_queries(title_variant: str, start: int, end: int) -> list[str]:
    """Stage 14.x: query shapes for a release bundling seasons `start`
    through `end` inclusive (`start < end`) — e.g. a "Reacher S01-S03"
    torrent covering every season aired so far while a later season is
    still airing. Mirrors `season_pack_queries`'s broadened set of
    real-world naming conventions (non-zero-padded numbering included) for
    the same confirmed-live reason: qBittorrent's search plugins don't do
    lenient full-text matching, so a query missing a release's actual
    formatting can simply never surface it, no matter how correct the
    pass-one gate would have been. The real filtering happens in
    `pack_score.py`'s pass-one gate, same as every other pack query
    here."""
    return [
        f"{title_variant} S{start:02d}-S{end:02d}",
        f"{title_variant} Seasons {start}-{end}",
        f"{title_variant} S{start}-S{end}",
        f"{title_variant} Season {start}-{end}",
    ]


def aired_cutoff_date(now: datetime | None = None, buffer_hours: float = 0.0) -> str:
    """The latest air_date treated as "already aired" — `buffer_hours`
    shifted back from `now` (default: the actual current UTC time, same
    convention as every other timestamp in this app — db.py/worker.py/
    tmdb.py all use `datetime.now(timezone.utc)`, never server-local)
    before taking the date, so a same-day air_date doesn't count as aired
    until that many hours have passed since UTC midnight on it. TMDB only
    ever gives a date, not a release time, and a real-world case (Ted
    Lasso S04E06, air_date 2026-09-08) found this app's own recheck cycle
    searching for an episode the *instant* the calendar date rolled over
    — hours before the show's actual release time, and long before any
    real torrent could plausibly exist yet, which is exactly the window
    fake/malicious releases get uploaded into to catch automated tools
    searching too early. `buffer_hours=0` (the exact-date behavior this
    replaced) disables the delay entirely.

    Note for tuning this setting: the comparison is anchored to UTC
    midnight, not the show's own release-market midnight — a platform
    releasing at 00:00 Pacific (UTC-7/-8) makes an episode genuinely
    available only ~7-8 hours *after* UTC midnight on its air_date,
    regardless of what time that is anywhere else (including wherever
    this app's household actually is). `buffer_hours` should cover that
    fixed offset plus however much longer real uploads realistically take
    to appear after the official release."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(hours=buffer_hours)).date().isoformat()


def episode_is_released(
    air_date: str | None,
    airstamp: datetime | None,
    now: datetime | None = None,
    buffer_hours: float = 0.0,
) -> bool:
    """Whether an episode is far enough past its release to search for.

    Two rules, because there are two qualities of input:

    - With an `airstamp` (TVmaze's exact UTC release moment) the buffer
      means what it says: released_at + buffer_hours. No anchor guessing,
      correct for a 21:00 ET broadcast and a 00:00 streaming drop alike.
    - Without one, the old date rule: TMDB gives only a date, so the
      buffer can only run from midnight UTC on it. That anchor is up to a
      day early for a US prime-time show, which is exactly why the
      airstamp path exists — but it is still better than nothing, and it
      is what every show TVmaze doesn't know falls back to.
    """
    now = now or datetime.now(timezone.utc)
    if airstamp is not None:
        return now >= airstamp + timedelta(hours=buffer_hours)
    return bool(air_date) and air_date <= aired_cutoff_date(now, buffer_hours)


def aired_episode_numbers(
    episodes: list[dict],
    now: datetime | None = None,
    buffer_hours: float = 0.0,
    airstamps: dict[int, datetime] | None = None,
) -> list[int]:
    """Episode numbers from a TMDB season's episode list that are out and
    past their buffer — shared by worker.py's check_show() (Stage 12) and
    pipeline.py's download_pack() (Stage 13), which both need to turn a
    raw TMDB season listing into "which episodes should actually exist by
    now".

    `airstamps` is `{episode_number: released_at_utc}` from TVmaze when
    it has the show; see `episode_is_released` for how the two rules
    differ and why the exact one is worth a second source."""
    now = now or datetime.now(timezone.utc)
    airstamps = airstamps or {}
    return [
        ep["episode_number"]
        for ep in episodes
        if ep.get("episode_number") is not None
        and episode_is_released(ep.get("air_date"), airstamps.get(ep["episode_number"]), now, buffer_hours)
    ]


def season_is_complete(
    episodes: list[dict],
    now: datetime | None = None,
    buffer_hours: float = 0.0,
    airstamps: dict[int, datetime] | None = None,
) -> bool:
    """True once every episode TMDB knows about for this season already
    has an air_date at or before `aired_cutoff_date` — i.e. the season has
    finished its run, not just "some episodes have aired so far". False
    for a season still actively releasing new episodes (an unaired or
    entirely unscheduled entry still remains), or one with no episodes
    listed at all (nothing to judge either way). See `aired_cutoff_date` for
    what `buffer_hours` does and why it exists.

    Used by worker.py's check_show() (Stage 14.x) to decide whether a
    season's still-unhandled aired episodes should be requested as a
    single season pack instead of individual per-episode searches — an
    older, fully-aired season is realistically far more likely to still
    have a well-seeded pack release than well-seeded individual episode
    releases, which tend to go cold once a show has moved on."""
    if not episodes:
        return False
    now = now or datetime.now(timezone.utc)
    airstamps = airstamps or {}
    return all(
        episode_is_released(ep.get("air_date"), airstamps.get(ep.get("episode_number")), now, buffer_hours)
        for ep in episodes
    )


def _finished_seasons(show: dict, today: str | None = None) -> int | None:
    """Seasons (specials excluded) that have started airing, minus the
    one `next_episode_to_air` says is still going. None without a season
    list."""
    seasons = [s for s in show.get("seasons") or [] if (s.get("season_number") or 0) >= 1]
    if not seasons:
        return None
    today = today or date.today().isoformat()
    started = {s["season_number"] for s in seasons if s.get("air_date") and s["air_date"] <= today}
    airing = (show.get("next_episode_to_air") or {}).get("season_number")
    return len(started - {airing})


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
        number_of_seasons=show.get("number_of_seasons") or None,
        finished_seasons=_finished_seasons(show),
        ended=show.get("status") in ("Ended", "Canceled"),
        first_air_year=first_air_year,
        poster_path=show.get("poster_path"),
        tvdb_id=(show.get("external_ids") or {}).get("tvdb_id"),
        imdb_id=(show.get("external_ids") or {}).get("imdb_id"),
    )


def episode_title_lookup(tmdb_id: int, tmdb: TMDBClient) -> Callable[[int, int], str | None]:
    """A `(season, episode) -> title` lookup for one show, fetching each
    season from TMDB at most once.

    The titles come from TMDB rather than the release's own filenames,
    which is a deliberate choice and not just the easier one. A release
    often has no title in the name at all (AOC's Secret Level pack names
    its files "S01E15.mkv"), and when it does have one it is the
    uploader's, in the uploader's language — PHDTeam's Love, Death &
    Robots names episode 1 "Tri roboti_ Strategie uniku". TMDB gives the
    same canonical name for every release of the same episode, so the
    library reads consistently no matter where a file came from.

    Returns None for anything TMDB can't answer for — an unnamed
    episode, a season it doesn't have, or the API being down — and
    build_episode_path then keeps the bare SxxEyy filename. A missing
    title is cosmetic; failing an organize over one would not be."""
    cache: dict[int, dict[int, str]] = {}

    def title_for(season: int, episode: int) -> str | None:
        if season not in cache:
            try:
                episodes = tmdb.get_tv_season(tmdb_id, season)
            except Exception:
                logger.info("episode titles unavailable for tmdb_id=%s season=%s", tmdb_id, season)
                episodes = []
            cache[season] = {
                e["episode_number"]: e.get("name") or ""
                for e in episodes
                if e.get("episode_number") is not None
            }
        return cache[season].get(episode) or None

    return title_for
