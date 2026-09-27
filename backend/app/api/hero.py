"""The landing pages' hero carousel."""

import concurrent.futures

from fastapi import Depends, HTTPException

from app.cache import ttl_cache
from app.db import RequestStore
from app.plex import plex_library_lookup
from app.tmdb import TMDBClient, TMDBError
from app.api.deps import get_store, get_tmdb, logger, resolve_region, router
from app.api.titles import get_movie_detail, get_tv_detail


# ---------------------------------------------------------------------------
# Landing-page hero slides.
#
# The carousel used to build these itself: fetch trending, then fire a
# detail call *per slide* purely to learn the title logo, badge, cert,
# length and genres, because TMDB's list endpoints carry none of those.
# That put two round trips in front of the logo before its <img> could
# even be created — the trending list, then the detail call — so the
# artwork arrived about a second after everything around it, and the
# whole thing repeated for every person on every page load. Folding it
# into one cached call is what takes the logo off the critical path.
#
# Nothing here recomputes what the detail routes already work out: it
# calls them, in parallel, and keeps the handful of fields a hero slide
# actually shows. The badge/cert/length wording stays in the frontend's
# homeHero.ts, the one place it has ever lived.
# ---------------------------------------------------------------------------

HERO_SLIDE_COUNT = 5
# Well past the trending lists these are drawn from: what a hero costs is
# five detail calls, and what it shows changes far more slowly than five
# minutes. At 300s a household loading Home a few times an hour found it
# cold nearly every time and paid 600ms for the privilege — measured on
# the real server, where it was the slowest thing on the page by a factor
# of three, everything else being served from cache in single-digit ms.
#
# What that leaves stale is `on_plex`, and that one is noticed: it is
# what turns the hero's button from "Add to Plex" into "Watch now", so a
# title you just added would go on denying it for half an hour. It is
# refreshed per request instead — see _with_fresh_plex_state, which costs
# nothing the page wasn't paying anyway.
HERO_TTL_SECONDS = 1800

# Only what a slide renders. The detail payloads these come from carry
# credits, recommendations and watch providers too — a hero showing five
# of those would be megabytes for the sake of a logo and two lines.
_HERO_COMMON = ("id", "overview", "backdrop_path", "poster_path", "logo_path", "genres", "is_coming_soon", "on_plex")
_HERO_MOVIE_ONLY = ("title", "runtime", "release_date", "release_dates")
_HERO_TV_ONLY = ("name", "first_air_date", "content_ratings", "next_episode_to_air", "last_episode_to_air", "plex_complete")


def _hero_slide(detail: dict, media_type: str) -> dict:
    keys = _HERO_COMMON + (_HERO_MOVIE_ONLY if media_type == "movie" else _HERO_TV_ONLY)
    slide = {k: detail.get(k) for k in keys}
    slide["media_type"] = media_type
    if media_type == "tv":
        # Season numbers are all the "N seasons" line needs; the full
        # objects carry an overview and poster art apiece.
        slide["seasons"] = [{"season_number": se.get("season_number")} for se in detail.get("seasons") or []]
    return slide


# How many of the five slides are held for something that isn't out yet.
# One, not zero and not a share of them: a hero that is all arrivals stops
# being the front of the library, and a hero with none of them is the
# state this was added to fix — a title can be the most popular thing on
# TMDB and still be invisible here, because the trending rows the hero
# drew from filter out anything without a digital release.
#
# Held rather than merged, because popularity alone doesn't guarantee it.
# It happened to on 2026-09-26 (Spider-Man: Brand New Day at 630 beat
# every trending title), but a quiet week has nothing arriving above the
# fold and the row would silently go back to what it was.
HERO_ARRIVING_SLOTS = 1

# How far ahead the hero looks for arrivals. Long enough that the slot is
# almost never empty, short enough that "available" still means soon: at
# 60 days the US calendar held 242 films on 2026-09-26.
HERO_ARRIVING_DAYS = 60


def _arriving_soon(kind: str, tmdb: TMDBClient, region: str) -> list[tuple[str, dict]]:
    """Titles about to become gettable — digital releases for films, a
    premiere date for shows.

    Films ask the household's region first and then the US, and the
    top-up is not optional padding: TMDB records digital street dates
    thoroughly for the US and barely at all elsewhere. Measured
    2026-09-26, the same 60-day window returned 14 titles for AU against
    242 for US, and the AU fourteen were obscure — the film this feature
    exists to surface had no Australian digital record at all. The same
    reasoning as releaseWindow.ts's cascade on the frontend: a release
    reaching the indexers is a worldwide event whatever a territory's own
    paperwork says.
    """
    found: list[tuple[str, dict]] = []
    if kind in ("home", "movies"):
        # Both scopes concatenated, duplicates and all: _hero_picks'
        # `usable` is the one place that dedupes, and having a second
        # place do it too is how the first version ended up with one
        # that was subtly weaker than the other.
        for scope in dict.fromkeys([region, "US"]):
            try:
                results = tmdb.get_digital_calendar(region=scope, days=HERO_ARRIVING_DAYS).get("results", [])
            except TMDBError:
                continue
            found += [("movie", item) for item in results if item.get("id")]
    if kind in ("home", "tv"):
        try:
            found += [("tv", it) for it in tmdb.get_tv_coming_soon(region=region).get("results", [])]
        except TMDBError:
            pass
    return found


def _hero_picks(kind: str, tmdb: TMDBClient, region: str) -> list[tuple[str, dict]]:
    """(media_type, list item) for the slides, most popular first. A title
    with no backdrop art can't be a hero, so that filter runs before the
    count is taken rather than after."""
    candidates: list[tuple[str, dict]] = []
    if kind in ("home", "movies"):
        candidates += [("movie", it) for it in tmdb.get_available_trending().get("results", [])]
    if kind in ("home", "tv"):
        candidates += [("tv", it) for it in tmdb.get_available_tv_trending().get("results", [])]

    def usable(pool: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
        """Backdrop-having, most popular first, each title once.

        The three belong together rather than at separate call sites.
        The first version sorted and filtered here but deduped only
        against the held id further down, and the two pools overlap by
        design — a film days from digital is exactly the kind that is
        also trending. So any overlapping title that wasn't the one held
        got two slides. Seen live: Spider-Man took the hold at 630 while
        The End of Oak Street sat in both pools at 384.6 and the movies
        hero showed it twice.
        """
        ranked = sorted(
            (c for c in pool if c[1].get("backdrop_path")),
            key=lambda c: c[1].get("popularity") or 0,
            reverse=True,
        )
        out: list[tuple[str, dict]] = []
        seen: set[tuple[str, int | None]] = set()
        for media_type, item in ranked:
            key = (media_type, item.get("id"))
            if key in seen:
                continue
            seen.add(key)
            out.append((media_type, item))
        return out

    arriving = usable(_arriving_soon(kind, tmdb, region))
    held = arriving[:HERO_ARRIVING_SLOTS]
    taken = {(mt, it.get("id")) for mt, it in held}

    # The rest of the hero is drawn from trending and the arrivals
    # together, so a second arrival can still earn a slot on merit rather
    # than being capped at the one that was held for it.
    rest = [c for c in usable(candidates + arriving) if (c[0], c[1].get("id")) not in taken]

    # Sorted as one list at the end: the held slide keeps its slot but not
    # a promoted position, so the carousel still opens on the most popular
    # thing unless the arrival happens to be it.
    return usable(held + rest[: HERO_SLIDE_COUNT - len(held)])


@ttl_cache(HERO_TTL_SECONDS)
def _hero_slides_cached(kind: str, region: str, store: RequestStore, tmdb: TMDBClient) -> list[dict]:
    """Keyed on the kind plus the two singletons on app.state, so this
    holds three entries rather than one per title — which matters,
    because app.cache's TTLCache has no size bound and only drops an
    entry when it is next read. A per-title cache here would grow
    without end."""
    picks = _hero_picks(kind, tmdb, region)
    if not picks:
        return []

    def build(media_type: str, item: dict) -> dict | None:
        try:
            detail = (
                get_movie_detail(item["id"], store, tmdb)
                if media_type == "movie"
                else get_tv_detail(item["id"], store, tmdb)
            )
        except Exception:
            # One title TMDB won't answer for shouldn't cost the carousel
            # its other four slides.
            logger.warning("hero slide failed for %s %s", media_type, item.get("id"), exc_info=True)
            return None
        return _hero_slide(detail, media_type)

    # In parallel: five sequential TMDB round trips is the very latency
    # this endpoint exists to remove, and doing them one after another
    # server-side would simply move it rather than fix it.
    with concurrent.futures.ThreadPoolExecutor(max_workers=HERO_SLIDE_COUNT) as pool:
        built = list(pool.map(lambda p: build(*p), picks))
    return [s for s in built if s]


def _with_fresh_plex_state(slides: list[dict], store: RequestStore) -> list[dict]:
    """Re-answer `on_plex` for slides that came out of the cache.

    Everything else a slide carries — the logo, the genres, the rating —
    is as true half an hour later as it was when TMDB was asked. Whether
    the thing is on Plex is not: it is the difference between the hero
    offering "Watch now" and offering to add something you already have.

    Cheap enough to do on every request because it is the same cached
    whole-library snapshot the rest of the page annotates itself from
    (see app/plex.py's plex_library_lookup), taken once per media type here
    rather than once per slide. Copies rather than mutates: these dicts
    are the cache's own."""
    matchers: dict[str, object] = {}
    fresh = []
    for slide in slides:
        media_type = slide["media_type"]
        if media_type not in matchers:
            matchers[media_type] = plex_library_lookup(store, "movie" if media_type == "movie" else "show")
        matcher = matchers[media_type]
        date = slide.get("release_date") or slide.get("first_air_date") or ""
        year = int(date[:4]) if date[:4].isdigit() else None
        title = slide.get("title") or slide.get("name") or ""
        fresh.append({**slide, "on_plex": bool(matcher(title, year, slide["id"])) if matcher else False})
    return fresh


@router.get("/api/hero")
def hero_slides(
    kind: str = "home",
    region: str = Depends(resolve_region),
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> list[dict]:
    """`kind` picks which landing page's carousel this is: mixed
    movies+TV for home, one or the other for the movies and TV pages."""
    if kind not in ("home", "movies", "tv"):
        raise HTTPException(status_code=422, detail="kind must be home, movies or tv")
    return _with_fresh_plex_state(_hero_slides_cached(kind, region, store, tmdb), store)
