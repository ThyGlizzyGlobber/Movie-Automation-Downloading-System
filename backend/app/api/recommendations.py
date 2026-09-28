"""Each landing page's discovery rows, filled and ordered for the person
looking at it.

Home, Movies and TV ask here once and get back every row that isn't their
own — the personal ones ("Because you watched…", Today's top picks, the
named rows) and the catalogue ones (Trending, Popular, Coming soon, a row
per genre and per service) — already ranked for them and dealt so no title
repeats down the page. feed.py is the logic; this is the plumbing: which
rows a page has, where their titles come from, and this person's signals.
"""

import concurrent.futures
import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from app import feed, language, moods, taste
from app.db import RequestStore, SessionRow
from app.plex import user_watch_history
from app.tmdb import TMDBClient
from app.api.deps import get_store, get_tmdb, require_session, resolve_region, router
from app.api.helpers import _annotate_on_plex

logger = logging.getLogger(__name__)


# What each landing page is made of around its discovery rows: which rows
# hold still at the top, which of the frontend's own rows are dealt with the
# rest, and whether the page is about one half of the catalogue.
#
# Pinning is per page because "what must not move" is a different question
# on each. Home pins New in your library second: a title that arrived today
# is news on exactly one day, and burying it is how a household misses it.
# TV pins Shows you follow instead — on a page about television, the shows
# you are actually mid-way through outrank anything discovery has to offer.
# Movies has no such row, so only Top 10 is fixed there.
#
# Continue watching is Home only. It spans both halves of the library, so
# it answers a question neither of the other two pages is asking.
#
# `own` are the frontend's rows that are dealt in with the rest (built from
# live data this module doesn't hold — the household's requests, followed
# shows), each with a fixed relevance: they're about the household, not a
# catalogue, so there's nothing to score them on.
PAGE_LAYOUTS = {
    "home": {
        "pinned": ("top10", "recent", "continue"),
        "own": {"requested": 0.55, "subscribed": 0.6},
        "media_type": None,
    },
    "movies": {
        "pinned": ("top10",),
        "own": {},
        "media_type": "movie",
    },
    "tv": {
        "pinned": ("top10", "subscribed"),
        "own": {},
        "media_type": "tv",
    },
}

# The catalogue rows each page carries. Home mixes films and shows in each
# genre row, so a genre there names both ids — they diverge (Sci-Fi is 878
# for films, 10765 for TV).
HOME_GENRES = (
    ("Comedies", 35, 35),
    ("Action & Adventure", 28, 10759),
    ("Sci-Fi & Fantasy", 878, 10765),
    ("Animation", 16, 16),
    ("Documentaries", 99, 99),
)
MOVIE_GENRES = ((28, "Action"), (35, "Comedies"), (27, "Horror"), (10749, "Romance"), (16, "Animation"), (99, "Documentaries"))
TV_GENRES = (
    (35, "Comedies"),
    (18, "Dramas"),
    (10765, "Sci-Fi & Fantasy"),
    (16, "Animation"),
    (99, "Documentaries"),
    (80, "Crime"),
    (10764, "Reality"),
)
# The "Popular on …" rows on Movies and TV: the first four of the
# frontend's curated services (lib/providers.ts). TMDB's provider ids are
# shared between films and shows.
ROW_PROVIDERS = ((8, "Netflix"), (9, "Prime Video"), (337, "Disney+"), (1899, "Max"))

# Pages of each source offered to its row. Popular lists overlap heavily, so
# a row whose first page has gone to the rows above needs a second to fill
# from; a third was measured to add nothing a row ever reached.
SOURCE_PAGES = 2

# How many of a person's seeds shape their taste. More than the day's rows
# (taste.ROWS_PER_DAY): the rows rotate through the pool, but the taste they
# are ranked against should be the whole of it, every day.
TASTE_SEEDS = taste.SEED_POOL

# Parallel lookups per page load — every catalogue page, seed and named
# row. Every call behind them is TTL-cached (tmdb.py), so this matters on a
# cold cache only, where a movie list's own release-date checks run eight
# at a time inside each job (tmdb.RELEASE_DATE_WORKERS). Bounded so one
# page can't open more connections than tmdb.HTTP_POOL_SIZE keeps.
FETCH_WORKERS = 12

_MAX_DECLARED_ROWS = 60
_MAX_ROW_KEY = 64


def _declared_rows(raw: str) -> list[str]:
    """Row keys a page says it has that this module doesn't fill.

    The page used to declare its genre and provider rows here so they could
    be dealt; those are filled here now, and a key that matches one simply
    names it. Kept so an older frontend and a newer backend still agree on
    a page. Echoed back inside `layout` and nowhere else, so the cap is
    about keeping a response sane; deduplicated because a key repeated would
    be dealt twice and render once."""
    seen: list[str] = []
    for key in raw.split(","):
        key = key.strip()
        if key and len(key) <= _MAX_ROW_KEY and key not in seen:
            seen.append(key)
        if len(seen) >= _MAX_DECLARED_ROWS:
            break
    return seen


def _tagged(items: list[dict], media_type: str) -> list[dict]:
    """Copies carrying their own media type. Copies because tmdb.py's
    cached methods hand back the same dicts on every hit."""
    return [{**item, "media_type": media_type} for item in items if item.get("id")]


@dataclass
class _CataloguePlan:
    """A page's catalogue rows before anything is fetched.

    `sources` is every (source, page) the rows need, each its own job so
    they all run at once — a source's second page no longer waits for its
    first. `rows` name which sources feed them: each group's pages run on
    from each other, and a mixed row alternates its groups (films, shows).
    `top10` is the page-one trending sources the Top 10 row is drawn from.
    """

    sources: dict[tuple, tuple[str, Callable[[], dict]]] = field(default_factory=dict)
    rows: list[tuple[str, str, str, str, dict, list[list[tuple]], bool]] = field(default_factory=list)
    top10: list[tuple] = field(default_factory=list)


def _catalogue_plan(page: str, tmdb: TMDBClient, region: str) -> _CataloguePlan:
    """Which catalogue rows the page carries and what feeds each one.

    Each source is the same cached call its row's "See all" page makes, so
    a row and its full list agree."""
    plan = _CataloguePlan()

    def want(name: str, media_type: str, fn, *args, pages: int = SOURCE_PAGES, **kwargs) -> list[tuple]:
        keys = []
        for n in range(1, pages + 1):
            key = (name, media_type, n)
            plan.sources[key] = (media_type, functools.partial(fn, *args, page=n, **kwargs))
            keys.append(key)
        return keys

    halves = ("movie", "tv") if page == "home" else (PAGE_LAYOUTS[page]["media_type"],)
    trending = {
        "movie": want("trending", "movie", tmdb.get_available_trending, time_window="week"),
        "tv": want("trending", "tv", tmdb.get_available_tv_trending, time_window="week"),
    }
    popular = {
        "movie": want("popular", "movie", tmdb.get_available_popular),
        "tv": want("popular", "tv", tmdb.get_available_tv_popular),
    }
    # One page: Coming soon barely overlaps anything else on a page (it was
    # the one row with no repeats before any of this), so a second page
    # would only ever be fetched, never shown — and it is the slowest
    # source there is.
    coming = {
        "movie": want("coming", "movie", tmdb.get_coming_soon, region=region, pages=1),
        "tv": want("coming", "tv", tmdb.get_tv_coming_soon, region=region, pages=1),
    }
    plan.top10 = [trending[half][0] for half in halves]

    if page == "home":
        plan.rows += [
            ("trending", "Trending", "now", "mixed", {"type": "movie", "sort": "trending"}, [trending["movie"], trending["tv"]], True),
            ("popular-movies", "Popular", "movies", "movie", {"type": "movie"}, [popular["movie"]], True),
            ("popular-tv", "Popular", "TV shows", "tv", {"type": "tv"}, [popular["tv"]], True),
            ("coming-soon", "Coming", "soon", "movie", {"type": "movie", "list": "coming-soon"}, [coming["movie"]], False),
        ]
        for label, movie_id, tv_id in HOME_GENRES:
            films = want(f"genre:{movie_id}", "movie", tmdb.get_available_by_genre, movie_id, region=region)
            shows = want(f"genre:{tv_id}", "tv", tmdb.get_available_tv_by_genre, tv_id, region=region)
            plan.rows.append((f"genre:{label}", label, "", "mixed", {"type": "movie", "genre": movie_id}, [films, shows], True))
    else:
        half = PAGE_LAYOUTS[page]["media_type"]
        by_genre = tmdb.get_available_by_genre if half == "movie" else tmdb.get_available_tv_by_genre
        by_provider = tmdb.get_available_by_provider if half == "movie" else tmdb.get_available_tv_by_provider
        plan.rows += [
            ("trending", "Trending", "movies" if half == "movie" else "shows", half, {"type": half, "sort": "trending"}, [trending[half]], True),
            ("popular", "Popular", "", half, {"type": half}, [popular[half]], True),
            ("coming-soon", "Coming", "soon", half, {"type": half, "list": "coming-soon"}, [coming[half]], False),
        ]
        for genre_id, label in MOVIE_GENRES if half == "movie" else TV_GENRES:
            keys = want(f"genre:{genre_id}", half, by_genre, genre_id, region=region)
            plan.rows.append((f"genre:{genre_id}", label, "", half, {"type": half, "genre": genre_id}, [keys], True))
        for provider_id, name in ROW_PROVIDERS:
            keys = want(f"provider:{provider_id}", half, by_provider, provider_id, region=region)
            plan.rows.append((f"provider:{provider_id}", "Popular", f"on {name}", half, {"type": half, "provider": provider_id}, [keys], True))

    # Only what this page's rows and Top 10 actually use is fetched.
    used = {key for row in plan.rows for group in row[5] for key in group} | set(plan.top10)
    plan.sources = {key: source for key, source in plan.sources.items() if key in used}
    return plan


def _submit_catalogue(plan: _CataloguePlan, pool: concurrent.futures.Executor) -> dict[tuple, concurrent.futures.Future]:
    return {key: pool.submit(fetch) for key, (_, fetch) in plan.sources.items()}


def _catalogue_rows(
    plan: _CataloguePlan, futures: dict[tuple, concurrent.futures.Future]
) -> tuple[list[feed.Candidate], set[tuple[str, int]]]:
    """The fetched catalogue as candidates, plus what the Top 10 shows. A
    source that failed costs its titles, never the page."""
    fetched: dict[tuple, list[dict]] = {}
    for key, future in futures.items():
        try:
            fetched[key] = _tagged(future.result().get("results") or [], plan.sources[key][0])
        except Exception as exc:  # noqa: BLE001 — one thin row, never the page
            logger.info("feed source %s unavailable: %s", key, exc)
            fetched[key] = []

    def run(group: list[tuple]) -> list[dict]:
        return [item for key in group for item in fetched.get(key, [])]

    candidates = [
        feed.Candidate(
            key=key,
            title=title,
            qualifier=qualifier,
            media_type=media_type,
            items=feed.interleave(*(run(group) for group in groups)),
            browse=browse,
            ranked=ranked,
        )
        for key, title, qualifier, media_type, browse, groups, ranked in plan.rows
    ]
    # The Top 10 row is the first ten of each half's trending list (see
    # TopTenRow.tsx). It is on the page before anything here is dealt.
    top10 = {feed.title_key(item, item["media_type"]) for key in plan.top10 for item in fetched.get(key, [])[:10]}
    return candidates, top10


def _seed_recommendations(
    seeds: list[taste.Seed], tmdb: TMDBClient, pool: concurrent.futures.Executor
) -> dict[tuple[str, int], list[dict]]:
    """TMDB's recommendations for each seed, fetched together. A seed whose
    lookup fails simply has none."""

    def fetch(seed: taste.Seed) -> list[dict]:
        fn = tmdb.get_tv_recommendations if seed.media_type == "tv" else tmdb.get_movie_recommendations
        return fn(seed.tmdb_id).get("results", []) or []

    futures = {(seed.media_type, seed.tmdb_id): pool.submit(fetch, seed) for seed in seeds}
    out: dict[tuple[str, int], list[dict]] = {}
    for key, future in futures.items():
        try:
            out[key] = future.result()
        except Exception:  # noqa: BLE001 — a dead seed shapes nothing, it breaks nothing
            out[key] = []
    return out


def _with_on_plex(rows: list[dict], store: RequestStore) -> list[dict]:
    """Mark what the household already has, rather than hiding it.

    A recommendation the house already owns is the most useful card on the
    page, not the least, because it is the one that can be watched right
    now. The badge is what turns it from "request this" into "this is here".

    Annotated once per media type for the whole page, not per row: each
    annotation builds its Plex matcher afresh, and that costs a settings
    read — about 0.08s on the NAS's volume — so per row it came to two
    seconds of a twenty-row page. Rows keep their own order.
    """

    def type_of(item: dict, row: dict) -> str:
        return item.get("media_type") or (row["media_type"] if row["media_type"] != "mixed" else "movie")

    by_type: dict[str, dict[int, dict]] = {}
    for row in rows:
        for item in row["items"]:
            by_type.setdefault(type_of(item, row), {})[int(item["id"])] = item
    marked: dict[tuple[str, int], dict] = {}
    for media_type, items in by_type.items():
        for item in _annotate_on_plex(
            list(items.values()),
            # TMDB calls it "tv"; Plex's library type is "show".
            "movie" if media_type == "movie" else "show",
            store,
            title_key="title" if media_type == "movie" else "name",
            date_key="release_date" if media_type == "movie" else "first_air_date",
        ):
            marked[(media_type, int(item["id"]))] = item
    return [
        {**row, "items": [marked.get((type_of(item, row), int(item["id"])), item) for item in row["items"]]}
        for row in rows
    ]


@router.get("/api/recommendations")
def get_recommendations(
    page: str = "home",
    rows_param: str = Query("", alias="rows"),
    region: str = Depends(resolve_region),
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    session: SessionRow = Depends(require_session),
) -> dict:
    """This person's discovery rows for today, and the order the page runs in.

    Their taste comes from three signals, strongest first: what they've
    watched on Plex (asked as them — see plex.user_watch_history), what
    they've requested, and which titles' pages they've opened here. Each
    decays with age (taste.py). Every catalogue row is then ranked against
    that taste and the page dealt so no title appears twice (feed.py).

    Per (person, day), decided here rather than in the browser, so an
    account gets the same page on its phone and its laptop and a reload
    never redeals it. `page` narrows it: Movies and TV get only their own
    half of the catalogue.

    Not separately cached: the TMDB lookups behind it are TTL-cached per
    title and per query, Plex history per person for ten minutes, and the
    rest is arithmetic. Degrades rather than errors — a source that fails
    costs its row, never the page."""
    if page not in PAGE_LAYOUTS:
        page = "home"
    # One pool for every lookup the page needs, and the catalogue — the
    # slowest part on a cold cache — submitted first, so it fetches while
    # this person's history is read and their seeds are looked up.
    with concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        plan = _catalogue_plan(page, tmdb, region)
        catalogue = _submit_catalogue(plan, pool)
        return _deal_page(page, rows_param, region, store, tmdb, session, pool, plan, catalogue)


def _deal_page(
    page: str,
    rows_param: str,
    region: str,
    store: RequestStore,
    tmdb: TMDBClient,
    session: SessionRow,
    pool: concurrent.futures.Executor,
    plan: _CataloguePlan,
    catalogue: dict[tuple, concurrent.futures.Future],
) -> dict:
    """get_recommendations' body, run with the catalogue already fetching."""
    layout_spec = PAGE_LAYOUTS[page]
    only = layout_spec["media_type"]
    now = datetime.now(timezone.utc)
    day = taste.today(now)
    who = session.plex_user_id

    history = user_watch_history(store, who, session.is_admin)
    seeds = taste.merge_seeds(
        taste.seeds_from_watch_history(history, now),
        taste.seeds_from_requests(store.list_requests_for_user(who), now),
        taste.seeds_from_views(store.list_title_views_for_user(who), now),
    )
    if only:
        seeds = [seed for seed in seeds if seed.media_type == only]
    # Everything they've watched is left out of what's recommended to them.
    seen = {(media_type, int(entry["tmdb_id"])) for media_type in ("movie", "tv") for entry in history.get(media_type) or []}

    seed_pool = seeds[:TASTE_SEEDS]
    recommended = _seed_recommendations(seed_pool, tmdb, pool)
    their_taste = feed.build_taste([(seed, recommended.get((seed.media_type, seed.tmdb_id), [])) for seed in seed_pool], seen)

    # "Because you watched…": today's handful of seeds from the pool, each
    # a row of what TMDB recommends from it.
    picked = taste.pick_seeds(seed_pool, taste.daily_rng(who, f"{day}:{page}", "seeds"))
    personal = taste.build_rows(
        picked,
        lambda media_type, tmdb_id: recommended.get((media_type, tmdb_id), []),
        taste.daily_rng(who, f"{day}:{page}", "items"),
        exclude=seen,
        # The whole list, not a window: the deal below picks from it.
        items_per_row=100,
    )
    candidates = [
        feed.Candidate(key=row.key, title=row.title, qualifier=row.qualifier, media_type=row.media_type, items=_tagged(row.items, row.media_type), personal=True)
        for row in personal
    ]

    # One blended row across everything the seeds suggested, ranked by how
    # many different seeds pointed at the same title. Agreement between two
    # of someone's interests is a better bet than the first entry of either
    # row alone.
    picks = taste.top_picks(personal)
    if len(picks) >= 4:
        candidates.append(feed.Candidate(key="top-picks", title="Today's top picks", qualifier="for you", media_type="mixed", items=picks, ranked=False, personal=True))

    # Named rows ("Comedies That Go Somewhere Dark"), steered by what their
    # seeds point at.
    affinity = moods.affinity_from_rows(personal)
    mood_catalogue = [m for m in moods.CATALOGUE if not only or m.media_type == only]
    chosen = moods.pick_moods(affinity, taste.daily_rng(who, f"{day}:{page}", "moods"), catalogue=mood_catalogue)
    # Asked in the household's language — see language.py for the
    # measurement that prompted it, and for why a mood that names its own
    # language (the Korean row) is left alone. All at once, on the pool.
    found = {
        mood.key: pool.submit(lambda m=mood: tmdb.discover_curated(m.media_type, **language.with_preferred_language(m.params, region)))
        for mood in chosen
    }
    for mood in chosen:
        try:
            items = found[mood.key].result().get("results", [])
        except Exception:  # noqa: BLE001 — one empty row, never the page
            continue
        # No qualifier: the name is the whole point, and "Quietly
        # Devastating · drama" would explain away the thing that made it
        # worth reading.
        candidates.append(feed.Candidate(key=mood.key, title=mood.title, qualifier="", media_type=mood.media_type, items=_tagged(items, mood.media_type)))

    catalogue_rows, top10 = _catalogue_rows(plan, catalogue)
    candidates += catalogue_rows
    by_key = {c.key: c for c in candidates}

    relevances = {key: feed.relevance(c, their_taste) for key, c in by_key.items()}
    relevances.update(layout_spec["own"])
    ordered = feed.order_rows(list(layout_spec["pinned"]), relevances, taste.daily_rng(who, f"{day}:{page}", "layout"))
    dealt = feed.deal(ordered, by_key, their_taste, claimed=top10)

    # Rows that couldn't fill are gone from the layout too; the frontend's
    # own rows and anything the page declared stay.
    layout = [key for key in ordered if key in dealt or key not in by_key]
    layout += [key for key in _declared_rows(rows_param) if key not in layout]
    rows = [
        {
            "key": key,
            "title": by_key[key].title,
            "qualifier": by_key[key].qualifier,
            "media_type": by_key[key].media_type,
            "items": dealt[key],
            "browse": by_key[key].browse,
        }
        for key in layout
        if key in dealt
    ]
    return {"day": day, "page": page, "rows": _with_on_plex(rows, store), "layout": layout}


class TitleView(BaseModel):
    media_type: Literal["movie", "tv"]
    tmdb_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)


@router.post("/api/views")
def record_title_view(body: TitleView, store: RequestStore = Depends(get_store), session: SessionRow = Depends(require_session)) -> dict:
    """Someone opened a title's page: the lightest of the three taste
    signals (see taste.SOURCE_WEIGHTS). Kept per person, one row per title,
    and never shown to anyone — it only tilts their own recommendations."""
    store.record_title_view(session.plex_user_id, body.media_type, body.tmdb_id, body.title)
    return {"recorded": True}
