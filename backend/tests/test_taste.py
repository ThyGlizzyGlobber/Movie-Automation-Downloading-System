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
