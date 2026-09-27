"""Per-person, per-day recommendation rows and the order each landing
page runs them in."""

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import Depends, Query

from app import language, moods, taste
from app.db import RequestStore, SessionRow
from app.tmdb import TMDBClient
from app.api.deps import get_store, get_tmdb, require_session, resolve_region, router
from app.api.helpers import _annotate_on_plex


# What each landing page is made of: which rows hold still at the top, which
# are dealt daily, and whether the page is about one half of the catalogue.
# Keys mirror the frontend's own — it ignores any it doesn't recognise and
# appends rows the layout never mentioned (the genre and provider rows,
# whose keys are ids the backend has no reason to know), so the two halves
# can deploy apart without either going blank.
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
PAGE_LAYOUTS = {
    "home": {
        "pinned": ("top10", "recent", "continue"),
        "rotating": ("trending", "requested", "popular-movies", "popular-tv", "coming-soon", "subscribed"),
        "media_type": None,
    },
    "movies": {
        "pinned": ("top10",),
        "rotating": ("trending", "popular", "coming-soon"),
        "media_type": "movie",
    },
    "tv": {
        "pinned": ("top10", "subscribed"),
        "rotating": ("trending", "popular", "coming-soon"),
        "media_type": "tv",
    },
}


# A page may carry rows this module has no name for — the genre rows, keyed
# by label, and the provider rows, keyed by a TMDB provider id. They are the
# frontend's to define and the frontend's to change, so rather than
# duplicating those lists here (and going stale the first time one moves),
# the page declares them and they join the deal. Bounded because it is
# user-supplied: enough for any page this app will have, and short enough
# that nothing interesting fits in one.
_MAX_DECLARED_ROWS = 60
_MAX_ROW_KEY = 64


def _declared_rows(raw: str) -> list[str]:
    """Row keys the page says it has, cleaned up.

    Echoed back inside `layout` and nowhere else — they never reach a
    query, a filesystem path or a template, so the cap is about keeping a
    response sane rather than holding off an attack. Deduplicated because a
    key repeated in the query would otherwise be dealt twice and render
    once, silently shortening the page."""
    seen: list[str] = []
    for key in raw.split(","):
        key = key.strip()
        if key and len(key) <= _MAX_ROW_KEY and key not in seen:
            seen.append(key)
        if len(seen) >= _MAX_DECLARED_ROWS:
            break
    return seen


def _with_on_plex(rows: list[taste.Row], store: RequestStore) -> list[taste.Row]:
    """Mark what the household already has, rather than hiding it.

    Every other discover row in app/api/ goes through _annotate_on_plex,
    and these should too — a recommendation the house already owns is the
    most useful card on the page, not the least, because it is the one that
    can be watched right now. The badge is what turns it from "request
    this" into "this is here".

    Grouped by media type rather than annotated item by item: the matcher
    behind it is built per library, so asking per item would rebuild it per
    item. The blended picks row is the only one carrying both, and its
    items say which they are.
    """
    annotated: list[taste.Row] = []
    for row in rows:
        by_type: dict[str, list[dict]] = {}
        for item in row.items:
            media_type = item.get("media_type") or (row.media_type if row.media_type != "mixed" else "movie")
            by_type.setdefault(media_type, []).append(item)
        marked: dict[int, dict] = {}
        for media_type, items in by_type.items():
            for item in _annotate_on_plex(
                items,
                # TMDB calls it "tv"; Plex's library type is "show".
                "movie" if media_type == "movie" else "show",
                store,
                title_key="title" if media_type == "movie" else "name",
                date_key="release_date" if media_type == "movie" else "first_air_date",
            ):
                marked[int(item["id"])] = item
        # Rebuilt in the row's own order — the grouping above is an
        # implementation detail and must not reach the page.
        annotated.append(
            taste.Row(
                key=row.key,
                title=row.title,
                qualifier=row.qualifier,
                media_type=row.media_type,
                items=[marked.get(int(item["id"]), item) for item in row.items],
            )
        )
    return annotated


@router.get("/api/recommendations")
def get_recommendations(
    page: str = "home",
    rows_param: str = Query("", alias="rows"),
    region: str = Depends(resolve_region),
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    session: SessionRow = Depends(require_session),
) -> dict:
    """This person's rows for today, and the order the page runs in.

    Both halves are per (person, day) and both are decided here rather than
    in the browser, so an account gets the same page on its phone and its
    laptop and a reload never redeals it.

    `page` narrows it: Movies and TV get only their own half of the
    catalogue, since a row of shows on the Movies page is a category error
    however good the recommendation is. Home takes both.

    Not separately cached: the TMDB lookups behind it are already TTL-cached
    per title and per query (see tmdb.py), and the rest is a bounded read of
    this person's own requests plus arithmetic. A second cache would mostly
    add a second thing that can serve yesterday.

    Degrades to empty rather than erroring — it decorates a page, it isn't a
    page. A household with nothing requested yet still gets the fixed rows,
    in a daily order, with named rows drawn from the whole catalogue."""
    layout_spec = PAGE_LAYOUTS.get(page) or PAGE_LAYOUTS["home"]
    only = layout_spec["media_type"]
    now = datetime.now(timezone.utc)
    day = taste.today(now)
    who = session.plex_user_id

    seeds = taste.seeds_from_requests(store.list_requests_for_user(who), now)
    if only:
        seeds = [seed for seed in seeds if seed.media_type == only]
    picked = taste.pick_seeds(seeds, taste.daily_rng(who, f"{day}:{page}", "seeds"))

    def recommend(media_type: str, tmdb_id: int) -> list[dict]:
        fetch = tmdb.get_tv_recommendations if media_type == "tv" else tmdb.get_movie_recommendations
        return fetch(tmdb_id).get("results", [])

    rows = taste.build_rows(picked, recommend, taste.daily_rng(who, f"{day}:{page}", "items"))

    # One blended row across everything the seeds suggested, ranked by how
    # many different seeds pointed at the same title. Agreement between two
    # of someone's interests is a better bet than the first entry of either
    # row alone, which is usually just the most popular thing in the genre.
    picks = taste.top_picks(rows)
    if len(picks) >= 4:
        rows.append(
            taste.Row(
                key="top-picks",
                title="Today's top picks",
                qualifier="for you",
                # Each item carries its own; see taste.top_picks.
                media_type="mixed",
                items=picks,
            )
        )

    # Named rows ("Comedies That Go Somewhere Dark"), steered by what the
    # rows above turned out to be made of. Read from those rather than
    # fetched: they already carry genre_ids and they are by construction
    # "things like what this person watches", so the affinity is free and
    # points somewhere adjacent to their taste rather than back at it.
    affinity = moods.affinity_from_rows(rows)
    catalogue = [m for m in moods.CATALOGUE if not only or m.media_type == only]
    for mood in moods.pick_moods(affinity, taste.daily_rng(who, f"{day}:{page}", "moods"), catalogue=catalogue):
        try:
            # Asked in the household's language — see language.py for the
            # measurement that prompted it, and for why a mood that names
            # its own language (the Korean row) is left alone.
            params = language.with_preferred_language(mood.params, region)
            found = tmdb.discover_curated(mood.media_type, **params).get("results", [])
        except Exception:  # noqa: BLE001 — one empty row, never the page
            continue
        items = [item for item in found if item.get("id")]
        if len(items) < 4:
            continue
        rows.append(
            taste.Row(
                key=mood.key,
                title=mood.title,
                # No qualifier: the name is the whole point, and "Quietly
                # Devastating · drama" would explain away the thing that
                # made it worth reading.
                qualifier="",
                media_type=mood.media_type,
                items=items[: taste.ITEMS_PER_ROW],
            )
        )

    ordered = taste.order_rows(
        list(layout_spec["pinned"]),
        [row.key for row in rows] + list(layout_spec["rotating"]) + _declared_rows(rows_param),
        taste.daily_rng(who, f"{day}:{page}", "layout"),
    )
    return {
        "day": day,
        "page": page,
        "rows": [asdict(row) for row in (_with_on_plex(rows, store))],
        "layout": ordered,
    }
