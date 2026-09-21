"""Resolves one selected TMDB show into a show identity and per-episode
search queries — the Stage 1 equivalent for TV. Family disambiguates
*which* show by tapping a poster; episode-level matching (Stage 10) is a
separate problem, same split as movies' resolve.py/score.py."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.normalize import generate_variants, token_overlap, tokenize
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


# Words that say nothing about *which* special a file is — they turn up
# in half of TMDB's special names and in most release filenames, so
# counting them as evidence would make everything match everything.
_UNINFORMATIVE = frozenset(
    {"the", "a", "an", "of", "and", "part", "special", "specials", "episode", "extra", "extras", "bonus"}
)

# A release's runtime never matches TMDB's exactly — ad breaks removed,
# a different cut, PAL speedup — so this only ever ranks candidates, and
# anything outside it simply stops being a tiebreak rather than being
# rejected.
_RUNTIME_TOLERANCE = 0.25


def _first_air_date(episodes: list[dict]) -> str | None:
    dates = sorted(e["air_date"] for e in episodes if e.get("air_date"))
    return dates[0] if dates else None


def _specials_in_window(cache: "_SeasonCache", season: int) -> list[dict]:
    """The specials that belong to this season's stretch of the show:
    everything in season 0 that aired after this season started and
    before the next one did.

    This is the step that makes the rest tractable. Doctor Who has 199
    specials; three of them aired in the year season 4 did. Without it,
    every later signal is picking out of a list nobody could pick out
    of, and position is meaningless.

    Season 1 has no lower bound, so a special that aired before the show
    premiered still belongs to it. A season with no next season has no
    upper bound. When air dates are missing on both sides the window
    cannot be drawn and every special stays a candidate — narrowing on
    absent data would be inventing it."""
    specials = [e for e in cache.raw(0) if e.get("episode_number") is not None]
    if not specials:
        return []
    start = _first_air_date(cache.raw(season)) if season > 1 else None
    nxt = _first_air_date(cache.raw(season + 1))
    if start is None and nxt is None:
        return specials
    windowed = [
        e
        for e in specials
        if e.get("air_date")
        and (start is None or e["air_date"] >= start)
        and (nxt is None or e["air_date"] < nxt)
    ]
    return windowed or specials


def _name_score(stem: str, special: dict, ignore: frozenset[str] = _UNINFORMATIVE) -> int:
    """How many informative words the filename and the special's title
    share. `Invincible.S01E09.Atom.Eve.1080p` against "PRESENTING ATOM
    EVE SPECIAL EPISODE" scores 2 — atom, eve — while "special" and
    "episode" are ignored on both sides.

    `ignore` carries the show's own name as well, and it has to. Every
    release filename leads with the show's title and plenty of specials
    repeat it, so counting those words scored
    "Doctor.Who.S04E14.The.Next.Doctor" equally against "The Next
    Doctor", "Doctor Who at the Proms" and "Doctor Who at Comic-Con
    2009" — a three-way tie on the word "doctor", which then fell
    through to position and picked the wrong one. Discounted, only
    "next" is left, and it points at exactly one of them."""
    name = special.get("name") or ""
    if not name or not stem:
        return 0
    shared = token_overlap(stem, name) - ignore
    return len(shared)


def _runtime_score(minutes: float | None, special: dict) -> float | None:
    """How close the file's own runtime is to TMDB's, as a fraction —
    lower is better. None when either side has no runtime, or when they
    are too far apart to mean anything."""
    listed = special.get("runtime")
    if not minutes or not listed:
        return None
    drift = abs(minutes - listed) / listed
    return drift if drift <= _RUNTIME_TOLERANCE else None


def _sole_best(scored: list[tuple[float, dict]]) -> dict | None:
    """The single best candidate, or None if the top two tie. A tie is
    not a decision, and pretending otherwise is how the wrong special
    gets picked confidently."""
    if not scored:
        return None
    ordered = sorted(scored, key=lambda pair: pair[0])
    if len(ordered) > 1 and ordered[0][0] == ordered[1][0]:
        return None
    return ordered[0][1]


class _SeasonCache:
    """One show's seasons, fetched from TMDB at most once each.

    A season TMDB cannot answer for caches as empty rather than raising.
    That distinction matters downstream: "this season has no episodes we
    know of" must never be read as "every episode in it is out of
    range", or an API outage would sweep a whole pack into Specials."""

    def __init__(self, tmdb_id: int, tmdb: TMDBClient):
        self._tmdb_id = tmdb_id
        self._tmdb = tmdb
        self._seasons: dict[int, list[dict]] = {}

    def raw(self, season: int) -> list[dict]:
        if season not in self._seasons:
            try:
                self._seasons[season] = self._tmdb.get_tv_season(self._tmdb_id, season)
            except Exception:
                logger.info("episode data unavailable for tmdb_id=%s season=%s", self._tmdb_id, season)
                self._seasons[season] = []
        return self._seasons[season]

    def episodes(self, season: int) -> dict[int, str]:
        return {
            e["episode_number"]: e.get("name") or ""
            for e in self.raw(season)
            if e.get("episode_number") is not None
        }

    def title(self, season: int, episode: int) -> str | None:
        return self.episodes(season).get(episode) or None


def episode_title_lookup(tmdb_id: int, tmdb: TMDBClient) -> Callable[[int, int], str | None]:
    """A `(season, episode) -> title` lookup for one show.

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
    cache = _SeasonCache(tmdb_id, tmdb)
    return cache.title


def _pick_special(
    candidates: list[dict],
    stem: str,
    get_duration: "Callable[[], float | None] | None",
    offset: int,
    ignore: frozenset[str] = _UNINFORMATIVE,
) -> tuple[dict | None, str]:
    """Which special a file is, out of the ones that could belong to
    this season — by name if the filename says, by runtime if it
    doesn't, by position if nothing else can tell them apart.

    Ordered by how much each signal actually knows. A filename carrying
    the title is direct evidence and beats everything. Runtime is
    circumstantial but often decisive, since specials in one window tend
    to differ wildly (Doctor Who's season-4 window holds a 7-minute
    Proms segment and a 61-minute Christmas episode). Position knows
    nothing except the order TMDB happens to list them in, so it goes
    last and only because something has to.

    A tie at any level is not a decision and falls through to the next
    signal rather than picking the first of the tied — Rick and Morty's
    thirty-seven specials are all listed at one minute, and several
    share a name."""
    if not candidates:
        return None, "nothing"
    if len(candidates) == 1:
        return candidates[0], "being the only special in this season's window"

    named = [(-_name_score(stem, c, ignore), c) for c in candidates if _name_score(stem, c, ignore)]
    best = _sole_best(named)
    if best is not None:
        return best, "the title in its filename"

    minutes = None
    if get_duration is not None:
        try:
            minutes = get_duration()
        except Exception:
            logger.info("could not read a runtime for %r; falling back to position", stem)
    if minutes:
        timed = [(d, c) for c in candidates if (d := _runtime_score(minutes, c)) is not None]
        best = _sole_best(timed)
        if best is not None:
            return best, f"runtime ({minutes:.0f}m)"

    if 1 <= offset <= len(candidates):
        return candidates[offset - 1], "position, nothing else being able to tell them apart"
    return None, "nothing"


def episode_placement_lookup(
    tmdb_id: int, tmdb: TMDBClient, show_title: str = ""
) -> Callable[..., tuple[int, int, str | None]]:
    """`(season, episode) -> (season, episode, title)` for a pack, where
    the season and episode coming back may not be the ones going in.

    Packs carry whatever the uploader numbered them, and a common scene
    habit is to append a special to the end of a season: Invincible's
    Atom Eve special ships inside season-1 packs as S01E09, where TMDB
    has it as S00E01 and season 1 stops at 8. Filed literally it lands
    in Season 01 as an episode Plex's agent has never heard of — no
    title, no artwork, no summary.

    So an episode numbered past the end of its season is read as the
    nth special: offset k = episode - (episodes TMDB lists for that
    season), placed at season 0 episode k.

    A pack holding more files than the season has episodes is holding
    extras, whatever it numbered them — so every one past the end is
    treated as a special, including offsets TMDB has no entry for. Those
    are placed in Season 00 untitled rather than left in the season as a
    phantom episode: unmatched either way, but sitting where a person
    would look for it.

    This is a positional guess and it is wrong on some shows. It is
    right whenever the extras were appended in the same order TMDB lists
    them, which is the usual case and is certain when there is only one.
    It is wrong for a pack carrying, say, the third special alone — that
    would be filed as the first. Chosen deliberately, with that
    understood.

    Two guards, because they cost nothing and one of them matters a lot:
      - only for seasons 1 and up, so a special already numbered s00 is
        left exactly as it is;
      - only for a season that has finished airing, and only when TMDB
        listed episodes for it at all. This is the important one. On a
        season still going out, TMDB's episode count is a moving target
        — a same-day release of episode 9 can easily arrive before TMDB
        has episode 9 — and remapping on a stale count would file a
        brand-new episode into Specials. An outage, which caches as an
        empty season, is refused by the same check.
    Every remap and every refusal is logged."""
    cache = _SeasonCache(tmdb_id, tmdb)
    ignore = _UNINFORMATIVE | set(tokenize(show_title))
    # Season-0 numbers already handed out by this lookup. One lookup
    # serves one pack, and two files in it must never be told they are
    # the same special: they would build the same path, and the second
    # would replace the first — a file silently lost out of a pack that
    # organized "successfully". A pack carrying two extras where TMDB
    # lists one special is exactly that case.
    assigned: set[int] = set()

    def place(
        season: int,
        episode: int,
        stem: str = "",
        get_duration: Callable[[], float | None] | None = None,
    ) -> tuple[int, int, str | None]:
        known = cache.episodes(season)
        if season < 1 or not known or episode in known:
            return season, episode, cache.title(season, episode)

        if not season_is_complete(cache.raw(season)):
            logger.warning(
                "tmdb_id=%s: s%02de%02d is past the %d episodes TMDB lists for season %d, but that season is "
                "still airing — leaving it alone rather than risking filing a new episode as a special",
                tmdb_id, season, episode, max(known), season,
            )
            return season, episode, None

        offset = episode - max(known)
        candidates = [
            c for c in _specials_in_window(cache, season) if c["episode_number"] not in assigned
        ]
        special, how = _pick_special(candidates, stem, get_duration, offset, frozenset(ignore))
        if special is None:
            # Still out of the season — an extra is an extra whether or
            # not TMDB lists one to pin it to — but at the first season-0
            # number nothing else has claimed, so two unpinnable extras
            # cannot land on one path.
            number = offset
            while number in assigned or number in cache.episodes(0):
                number += 1
            assigned.add(number)
            logger.info(
                "tmdb_id=%s: s%02de%02d is past the end of season %d — no unclaimed special on TMDB to pin "
                "it to, filing it as s00e%02d untitled",
                tmdb_id, season, episode, season, number,
            )
            return 0, number, None

        assigned.add(special["episode_number"])

        logger.info(
            "tmdb_id=%s: s%02de%02d is past the end of season %d (%d episodes) — filing it as s00e%02d %r, "
            "matched by %s out of %d special(s) in this season's window",
            tmdb_id, season, episode, season, max(known),
            special["episode_number"], special.get("name"), how, len(candidates),
        )
        return 0, special["episode_number"], special.get("name") or None

    return place
