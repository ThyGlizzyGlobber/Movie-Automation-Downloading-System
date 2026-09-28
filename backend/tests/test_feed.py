import random

from app import feed
from app.taste import Seed


def _items(ids, genres=(18,), media_type=None):
    out = [{"id": n, "genre_ids": list(genres), "popularity": 100 - i} for i, n in enumerate(ids)]
    if media_type:
        for item in out:
            item["media_type"] = media_type
    return out


def _row(key, ids, media_type="movie", **kwargs):
    return feed.Candidate(key=key, title=key, qualifier="", media_type=media_type, items=_items(ids), **kwargs)


def test_no_title_is_dealt_twice_down_the_page():
    """The whole point: popular lists overlap, and the page used to show the
    same film in five rows."""
    rows = {k: _row(k, range(100, 140)) for k in ("trending", "popular", "genre:18")}

    dealt = feed.deal(list(rows), rows, feed.Taste())

    ids = [item["id"] for row in dealt.values() for item in row]
    assert len(ids) == len(set(ids))
    # And each row still fills: 40 candidates is enough for two rows of 20.
    assert [len(dealt[k]) for k in ("trending", "popular")] == [20, 20]


def test_higher_rows_get_first_pick():
    rows = {"a": _row("a", range(1, 30)), "b": _row("b", range(1, 30))}

    dealt = feed.deal(["a", "b"], rows, feed.Taste())

    assert [i["id"] for i in dealt["a"]][:3] == [1, 2, 3]
    assert dealt["b"][0]["id"] == 21


def test_what_the_top_10_shows_is_not_shown_again():
    rows = {"trending": _row("trending", range(1, 40))}

    dealt = feed.deal(["trending"], rows, feed.Taste(), claimed={("movie", n) for n in range(1, 11)})

    assert dealt["trending"][0]["id"] == 11


def test_films_and_shows_with_the_same_id_are_different_titles():
    rows = {"m": _row("m", range(1, 10), "movie"), "t": _row("t", range(1, 10), "tv")}

    dealt = feed.deal(["m", "t"], rows, feed.Taste())

    assert len(dealt["m"]) == len(dealt["t"]) == 9


def test_a_row_too_thin_to_show_gives_its_titles_back():
    """A three-title row reads as a bug; dropping it must not also starve
    the row below of those titles."""
    rows = {"thin": _row("thin", [1, 2, 3]), "below": _row("below", [1, 2, 3, 4, 5, 6])}

    dealt = feed.deal(["thin", "below"], rows, feed.Taste())

    assert "thin" not in dealt
    assert [i["id"] for i in dealt["below"]] == [1, 2, 3, 4, 5, 6]


def test_what_they_have_watched_is_never_recommended():
    rows = {"popular": _row("popular", range(1, 30))}
    seen = frozenset({("movie", 1), ("movie", 2)})

    dealt = feed.deal(["popular"], rows, feed.Taste(seen=seen))

    assert {1, 2}.isdisjoint(i["id"] for i in dealt["popular"])


def test_without_a_history_a_row_keeps_its_own_order():
    rows = {"trending": _row("trending", [5, 4, 3, 2, 1, 9, 8])}

    dealt = feed.deal(["trending"], rows, feed.Taste())

    assert [i["id"] for i in dealt["trending"]] == [5, 4, 3, 2, 1, 9, 8]


def test_what_their_history_points_at_rises_within_a_row():
    """A title their own seeds recommend is the best bet on the row, even
    from further down its list."""
    taste = feed.Taste(genres={18: 1.0}, suggested={("movie", 9): 1.0})
    rows = {"popular": _row("popular", [1, 2, 3, 4, 5, 6, 7, 8, 9])}

    dealt = feed.deal(["popular"], rows, taste)

    assert dealt["popular"][0]["id"] == 9


def test_genre_fit_lifts_a_title_but_does_not_flatten_the_list():
    taste = feed.Taste(genres={27: 1.0})
    row = feed.Candidate(
        key="popular",
        title="",
        qualifier="",
        media_type="movie",
        items=[{"id": 1, "genre_ids": [35]}, {"id": 2, "genre_ids": [35]}, {"id": 3, "genre_ids": [27]}],
    )

    dealt = feed.deal(["popular"], {"popular": row}, taste, min_items=1)

    assert [i["id"] for i in dealt["popular"]] == [3, 1, 2]


def test_a_shows_genres_count_toward_the_films_it_resembles():
    """TV's "Sci-Fi & Fantasy" is one genre where film has two. Someone who
    watches sci-fi films should still see sci-fi shows ranked up."""
    taste = feed.Taste(genres={878: 1.0})

    assert feed.genre_fit({"genre_ids": [10765]}, taste) > 0


def test_an_unranked_row_keeps_its_order_whatever_the_taste():
    """Coming soon is by date; ranking it would scramble when things land."""
    taste = feed.Taste(genres={27: 1.0}, suggested={("movie", 3): 1.0})
    rows = {"coming-soon": _row("coming-soon", [1, 2, 3, 4, 5, 6], ranked=False)}

    dealt = feed.deal(["coming-soon"], rows, taste)

    assert [i["id"] for i in dealt["coming-soon"]] == [1, 2, 3, 4, 5, 6]


def test_the_rows_that_suit_them_come_first():
    order = feed.order_rows(["top10"], {"horror": 0.2, "drama": 0.9, "comedy": 0.5}, random.Random(1), jitter=0.0)

    assert order == ["top10", "drama", "comedy", "horror"]


def test_the_order_is_fixed_for_a_day_and_nudged_between_days():
    relevances = {f"row{n}": 0.5 for n in range(12)}

    same = [feed.order_rows([], relevances, random.Random(7)) for _ in range(2)]
    other = feed.order_rows([], relevances, random.Random(8))

    assert same[0] == same[1]
    assert other != same[0]


def test_taste_adds_up_where_seeds_agree_and_leaves_the_seed_out():
    heat = Seed(tmdb_id=1, media_type="movie", title="Heat", weight=1.0, source="watched")
    sicario = Seed(tmdb_id=2, media_type="movie", title="Sicario", weight=1.0, source="watched")
    taste = feed.build_taste(
        [
            (heat, [{"id": 1, "genre_ids": [80]}, {"id": 10, "genre_ids": [80]}, {"id": 11, "genre_ids": [80]}]),
            (sicario, [{"id": 10, "genre_ids": [80]}, {"id": 12, "genre_ids": [53]}]),
        ]
    )

    assert ("movie", 1) not in taste.suggested
    assert taste.suggested[("movie", 10)] == 1.0
    assert taste.suggested[("movie", 11)] < 1.0
    assert taste.genres[80] == 1.0


def test_a_rows_relevance_ignores_what_they_have_seen():
    seen = frozenset({("movie", n) for n in range(1, 11)})
    taste = feed.Taste(genres={18: 1.0}, seen=seen)
    watched_head = _row("a", list(range(1, 11)) + [50, 51])

    unseen = [feed.score(item, "movie", rank, taste) for rank, item in enumerate(watched_head.items) if item["id"] >= 50]
    assert feed.relevance(watched_head, taste) == sum(unseen) / len(unseen)


def test_interleave_alternates_and_keeps_the_tail():
    assert [i["id"] for i in feed.interleave(_items([1, 2, 3]), _items([7]))] == [1, 7, 2, 3]


def test_a_because_row_is_never_starved_by_a_row_above_it():
    """A named row higher up the page once took every title a "Because you
    watched" row had, and the row explaining itself vanished."""
    rows = {
        "mood": _row("mood", range(1, 21)),
        "because": _row("because", range(1, 21), personal=True),
    }

    dealt = feed.deal(["mood", "because"], rows, feed.Taste())

    assert [i["id"] for i in dealt["because"]] == list(range(1, 21))
    assert "mood" not in dealt
