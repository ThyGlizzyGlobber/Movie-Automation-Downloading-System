"""Recommendation rows: what gets seeded, and what makes the page move.

Written against the pure module rather than the endpoint, because the thing
worth pinning here is judgement — how much a month-old request counts, that
twenty episodes of one show are one opinion, that the page differs tomorrow
but not between two reloads a minute apart. None of that needs a Plex
server, a TMDB key or a browser to be wrong.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import taste


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


@dataclass
class FakeRequest:
    """Just the fields seeds_from_requests reads — a real RequestRow has
    thirty, and depending on the other twenty-odd here would make this test
    fail for reasons that have nothing to do with recommendations."""

    tmdb_id: int
    title: str
    media_type: str
    created_at: str


def _days_ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def test_recent_requests_outweigh_old_ones():
    seeds = taste.seeds_from_requests(
        [
            FakeRequest(1, "Ancient", "movie", _days_ago(400)),
            FakeRequest(2, "Yesterday", "movie", _days_ago(1)),
        ],
        NOW,
    )

    assert [s.title for s in seeds] == ["Yesterday", "Ancient"]
    # Decayed, not discarded: a year-old favourite still says something.
    assert seeds[-1].weight > 0


def test_every_episode_of_one_show_is_a_single_opinion():
    """Someone who requested a whole season has one interest and twenty
    rows in `requests`. Left uncollapsed that show would fill the pool and
    crowd out everything else they like."""
    seeds = taste.seeds_from_requests(
        [FakeRequest(99, "Severance", "episode", _days_ago(n)) for n in range(1, 21)]
        + [FakeRequest(99, "Severance", "pack", _days_ago(3))]
        + [FakeRequest(7, "Heat", "movie", _days_ago(2))],
        NOW,
    )

    assert sorted((s.media_type, s.tmdb_id) for s in seeds) == [("movie", 7), ("tv", 99)]


def test_a_request_row_with_nothing_to_seed_is_skipped():
    seeds = taste.seeds_from_requests(
        [
            FakeRequest(None, "No id", "movie", _days_ago(1)),
            FakeRequest(5, "Odd type", "documentary", _days_ago(1)),
            FakeRequest(6, "Fine", "movie", _days_ago(1)),
        ],
        NOW,
    )

    assert [s.tmdb_id for s in seeds] == [6]


def test_watching_and_asking_for_the_same_thing_counts_for_more():
    """The strongest statement someone can make about a title is to ask for
    it and then actually watch it, and that should be what their page leans
    on."""
    requested = [taste.Seed(1, "movie", "Dune", 0.8, "requested")]
    watched = [taste.Seed(1, "movie", "Dune", 1.0, "watched")]

    merged = taste.merge_seeds(requested, watched)

    assert len(merged) == 1
    assert merged[0].weight > 1.0
    # "Because you watched" is the more honest sentence of the two.
    assert merged[0].source == "watched"


def test_the_same_person_sees_the_same_rows_all_day():
    """Two page loads a minute apart must agree. Rotation that reshuffled
    per request would be worse than no rotation — a row that moves while
    you're reading it reads as a bug."""
    seeds = [taste.Seed(i, "movie", f"Film {i}", 1.0 - i / 100, "requested") for i in range(12)]

    first = taste.pick_seeds(seeds, taste.daily_rng("user-1", "2026-09-24"))
    second = taste.pick_seeds(seeds, taste.daily_rng("user-1", "2026-09-24"))

    assert [s.tmdb_id for s in first] == [s.tmdb_id for s in second]


def test_the_page_turns_over_across_days_and_differs_between_people():
    seeds = [taste.Seed(i, "movie", f"Film {i}", 1.0 - i / 100, "requested") for i in range(12)]

    days = {
        tuple(s.tmdb_id for s in taste.pick_seeds(seeds, taste.daily_rng("user-1", f"2026-09-{d:02d}")))
        for d in range(1, 15)
    }
    mine = tuple(s.tmdb_id for s in taste.pick_seeds(seeds, taste.daily_rng("user-1", "2026-09-24")))
    theirs = tuple(s.tmdb_id for s in taste.pick_seeds(seeds, taste.daily_rng("user-2", "2026-09-24")))

    assert len(days) > 1, "a page that never changes is the thing this exists to fix"
    assert mine != theirs, "two people on one server shouldn't get one page"


def test_rotation_never_reaches_past_the_strongest_seeds():
    """Freshness is drawn from the pool, not from the whole history — every
    row still has to be one this person would recognise as theirs."""
    seeds = [taste.Seed(i, "movie", f"Film {i}", 1.0 - i / 100, "requested") for i in range(40)]

    for day in range(1, 15):
        picked = taste.pick_seeds(seeds, taste.daily_rng("user-1", f"2026-09-{day:02d}"))
        assert all(s.tmdb_id < taste.SEED_POOL for s in picked)
        assert len(picked) == taste.ROWS_PER_DAY


def test_a_short_history_still_produces_rows():
    """One request is a thin profile, not an error — the page should say
    something rather than nothing."""
    seeds = [taste.Seed(1, "movie", "Only One", 1.0, "requested")]

    assert [s.tmdb_id for s in taste.pick_seeds(seeds, taste.daily_rng("u", "2026-09-24"))] == [1]


def test_rows_never_recommend_what_they_already_have():
    """TMDB's co-watch data has no idea what's in your library, so it will
    cheerfully recommend the film you asked for last week. That is the most
    obvious way for this feature to look broken."""
    seeds = [taste.Seed(1, "movie", "Dune", 1.0, "watched")]
    catalogue = [{"id": n, "title": f"Film {n}"} for n in range(2, 30)]

    rows = taste.build_rows(
        seeds,
        recommend=lambda media_type, tmdb_id: catalogue,
        rng=taste.daily_rng("u", "2026-09-24"),
        exclude={("movie", 5), ("movie", 6)},
    )

    ids = {item["id"] for item in rows[0].items}
    assert 5 not in ids and 6 not in ids


def test_a_seed_with_almost_no_recommendations_is_dropped():
    """A row of two reads as a bug rather than a short list."""
    seeds = [taste.Seed(1, "movie", "Obscure", 1.0, "watched")]

    rows = taste.build_rows(
        seeds,
        recommend=lambda media_type, tmdb_id: [{"id": 2}, {"id": 3}],
        rng=taste.daily_rng("u", "2026-09-24"),
    )

    assert rows == []


def test_one_dead_seed_costs_its_row_and_not_the_page():
    """TMDB being unreachable for one title must not empty Home."""
    def flaky(media_type, tmdb_id):
        if tmdb_id == 1:
            raise RuntimeError("TMDB said no")
        return [{"id": n} for n in range(10, 40)]

    seeds = [
        taste.Seed(1, "movie", "Breaks", 1.0, "watched"),
        taste.Seed(2, "movie", "Works", 0.9, "watched"),
    ]

    rows = taste.build_rows(seeds, recommend=flaky, rng=taste.daily_rng("u", "2026-09-24"))

    assert [r.qualifier for r in rows] == ["Works"]


def test_a_row_says_why_it_is_there():
    """The justification is the feature. A row of films with no stated
    reason is indistinguishable from another Popular row."""
    seeds = [taste.Seed(603, "movie", "The Matrix", 1.0, "watched")]

    rows = taste.build_rows(
        seeds,
        recommend=lambda media_type, tmdb_id: [{"id": n} for n in range(10, 40)],
        rng=taste.daily_rng("u", "2026-09-24"),
    )

    assert rows[0].title == "Because you watched"
    assert rows[0].qualifier == "The Matrix"
    assert rows[0].key == "because:movie:603"


def test_rotation_survives_a_restart():
    """The generator is seeded from a hash, not Python's per-process
    `hash()` — otherwise every backend restart would reshuffle the
    household's rows, which is the opposite of the point."""
    first = taste.daily_rng("user-1", "2026-09-24").random()
    second = taste.daily_rng("user-1", "2026-09-24").random()

    assert first == second


# -- Page layout: which row sits where, day to day ------------------------

ROTATING = ["because:movie:1", "because:tv:2", "popular-movies", "popular-tv", "coming-soon", "new-in-library"]
PINNED = ["continue-watching", "trending"]


def test_the_page_is_dealt_fresh_each_day():
    """Without this, only the personalised rows move: Popular movies is
    forever above Popular TV, forever above Coming soon, and the whole page
    reads as the same page with two new rows in it."""
    orders = {
        tuple(taste.order_rows(PINNED, ROTATING, taste.daily_rng("u", f"2026-09-{d:02d}", "layout")))
        for d in range(1, 15)
    }

    assert len(orders) > 1


def test_continue_watching_and_trending_never_move():
    """These two are navigation, not browsing — resuming what you were in
    the middle of shouldn't involve hunting for the row first."""
    for day in range(1, 15):
        order = taste.order_rows(PINNED, ROTATING, taste.daily_rng("u", f"2026-09-{day:02d}", "layout"))
        assert order[:2] == ["continue-watching", "trending"]


def test_dealing_the_page_neither_loses_nor_duplicates_a_row():
    order = taste.order_rows(PINNED, ROTATING, taste.daily_rng("u", "2026-09-24", "layout"))

    assert sorted(order) == sorted(PINNED + ROTATING)


def test_the_layout_holds_still_within_a_day_and_differs_between_people():
    first = taste.order_rows(PINNED, ROTATING, taste.daily_rng("u", "2026-09-24", "layout"))
    second = taste.order_rows(PINNED, ROTATING, taste.daily_rng("u", "2026-09-24", "layout"))
    other = taste.order_rows(PINNED, ROTATING, taste.daily_rng("someone-else", "2026-09-24", "layout"))

    assert first == second
    assert first != other


def test_drawing_seeds_and_dealing_the_page_are_independent():
    """Same person, same day, different questions — they must not share a
    stream, or changing how many rows we draw would silently redeal the
    layout too."""
    seeds_stream = taste.daily_rng("u", "2026-09-24", "seeds").random()
    layout_stream = taste.daily_rng("u", "2026-09-24", "layout").random()

    assert seeds_stream != layout_stream


# -- Top picks: what several of your seeds agreed on ----------------------


def test_top_picks_favours_what_several_seeds_agreed_on():
    """A title pointed at from two directions is a better bet than either
    row's own first entry, which is usually just whatever is most popular
    in that genre."""
    rows = [
        taste.Row("a", "Because you watched", "Heat", "movie",
                  [{"id": 1, "popularity": 5}, {"id": 2, "popularity": 90}]),
        taste.Row("b", "Because you watched", "Sicario", "movie",
                  [{"id": 1, "popularity": 5}, {"id": 3, "popularity": 80}]),
    ]

    picks = taste.top_picks(rows)

    assert picks[0]["id"] == 1, "agreement should beat raw popularity"


def test_top_picks_breaks_ties_on_popularity():
    rows = [
        taste.Row("a", "t", "A", "movie", [{"id": 1, "popularity": 10}]),
        taste.Row("b", "t", "B", "movie", [{"id": 2, "popularity": 99}]),
    ]

    assert [p["id"] for p in taste.top_picks(rows)] == [2, 1]


def test_top_picks_carries_each_items_own_media_type():
    """The one row where films and shows sit together, so the row's own
    media_type can't answer for them — a show routed to a film's detail
    page reads as a broken link, not a bad recommendation."""
    rows = [
        taste.Row("a", "t", "Heat", "movie", [{"id": 1, "popularity": 9}]),
        taste.Row("b", "t", "Fargo", "tv", [{"id": 2, "popularity": 8}]),
    ]

    picks = taste.top_picks(rows)

    assert {p["id"]: p["media_type"] for p in picks} == {1: "movie", 2: "tv"}


def test_top_picks_needs_more_than_one_row_to_mean_anything():
    """With a single row, "what your seeds agreed on" is that row again
    wearing a grander name."""
    rows = [taste.Row("a", "t", "A", "movie", [{"id": n} for n in range(10)])]

    assert taste.top_picks(rows) == []


def test_a_seed_never_opens_its_own_row():
    """"Because you watched Heat" leading with Heat is the one exclusion
    that is always right, however good the co-watch data is."""
    seeds = [taste.Seed(42, "movie", "Heat", 1.0, "watched")]

    rows = taste.build_rows(
        seeds,
        recommend=lambda media_type, tmdb_id: [{"id": n} for n in range(40, 70)],
        rng=taste.daily_rng("u", "2026-09-24"),
    )

    assert 42 not in {item["id"] for item in rows[0].items}


def test_titles_the_household_already_has_are_not_hidden():
    """This app is where someone decides what to watch tonight, not only
    what to download — hiding what is already downloaded answers a question
    nobody asked."""
    seeds = [taste.Seed(1, "movie", "Heat", 1.0, "watched")]
    owned = [{"id": n} for n in range(10, 40)]

    rows = taste.build_rows(
        seeds,
        recommend=lambda media_type, tmdb_id: owned,
        rng=taste.daily_rng("u", "2026-09-24"),
    )

    assert len(rows[0].items) >= 4
