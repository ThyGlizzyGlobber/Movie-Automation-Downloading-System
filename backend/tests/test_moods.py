"""Named rows: do they suit the person, do they move, and is the catalogue
itself sound.

The last one matters more than it looks. These rows are hand-written, so
the failure mode isn't a crash — it's a row called "Quietly Devastating"
quietly filling with films three people have rated, which looks like a bug
in the recommender when it is really a missing vote floor.
"""

import random
from collections import Counter

from app import moods


class FakeRow:
    def __init__(self, items):
        self.items = items


def test_affinity_is_read_from_rows_already_built():
    """Free by construction: the rows are already fetched, every TMDB
    result carries genre_ids, so knowing what someone's page is made of
    costs no extra call."""
    rows = [
        FakeRow([{"id": 1, "genre_ids": [27, 53]}, {"id": 2, "genre_ids": [27]}]),
        FakeRow([{"id": 3, "genre_ids": [35]}]),
    ]

    affinity = moods.affinity_from_rows(rows)

    assert affinity[27] == 2
    assert affinity[53] == 1
    assert affinity[35] == 1


def test_affinity_survives_results_with_no_genres():
    rows = [FakeRow([{"id": 1}, {"id": 2, "genre_ids": None}, {"id": 3, "genre_ids": [18]}])]

    assert moods.affinity_from_rows(rows) == Counter({18: 1})


def test_someone_who_watches_horror_is_offered_horror_rows():
    horror_fan = Counter({moods.HORROR: 40, moods.THRILLER: 25})

    picked = moods.pick_moods(horror_fan, random.Random(0), count=3, pool=4)

    assert any(moods.HORROR in mood.genres for mood in picked)


def test_a_brand_new_household_still_gets_a_full_page():
    """No requests yet means no affinity. The right answer is a varied,
    well-named page — not an empty one waiting for them to earn it."""
    picked = moods.pick_moods(Counter(), random.Random(0))

    assert len(picked) == moods.MOODS_PER_DAY
    assert len({mood.key for mood in picked}) == moods.MOODS_PER_DAY


def test_named_rows_hold_still_within_a_day_and_move_across_days():
    affinity = Counter({moods.DRAMA: 10, moods.CRIME: 8})

    same_day = [
        tuple(m.key for m in moods.pick_moods(affinity, random.Random(1234)))
        for _ in range(2)
    ]
    across_days = {
        tuple(m.key for m in moods.pick_moods(affinity, random.Random(seed)))
        for seed in range(12)
    }

    assert same_day[0] == same_day[1]
    assert len(across_days) > 1


def test_taste_narrows_the_pool_without_emptying_it():
    """A strong preference should shape the page, not reduce it to one
    genre — the pool is what keeps a horror fan from being shown only
    horror until they watch something else."""
    affinity = Counter({moods.HORROR: 100})

    seen = {
        m.key
        for seed in range(30)
        for m in moods.pick_moods(affinity, random.Random(seed))
    }

    assert len(seen) > moods.MOODS_PER_DAY


def test_every_mood_has_a_name_worth_reading():
    """A row called "Comedy" is a filing cabinet. If one of these ends up
    named after its genre, the feature has quietly stopped being the
    feature."""
    plain = {"comedy", "drama", "horror", "action", "documentary", "romance", "western", "thriller"}

    for mood in moods.CATALOGUE:
        assert mood.title.lower() not in plain, mood.key
        assert len(mood.title.split()) >= 2, mood.key


def test_every_quality_filter_has_a_vote_floor_under_it():
    """"Highly rated" with no vote floor reliably surfaces an unreleased
    film with a single 10 — the most embarrassing possible contents for a
    row called "Quietly Devastating"."""
    for mood in moods.CATALOGUE:
        if "vote_average.gte" in mood.params:
            assert mood.params.get("vote_count.gte", 0) >= 100, mood.key


def test_the_catalogue_has_no_duplicate_keys_and_valid_namespaces():
    """Keys are React's row identity, and media_type decides which half of
    TMDB is asked — a wrong one returns a confident, unrelated list."""
    keys = [mood.key for mood in moods.CATALOGUE]

    assert len(keys) == len(set(keys))
    assert all(mood.media_type in ("movie", "tv") for mood in moods.CATALOGUE)
    assert all(mood.params for mood in moods.CATALOGUE)


def test_tv_moods_never_use_movie_only_filters():
    """TMDB splits these: a movie's date is primary_release_date, a show's
    is first_air_date, and sending the wrong one is silently ignored — the
    row renders, unfiltered, looking merely mediocre."""
    for mood in moods.CATALOGUE:
        if mood.media_type == "tv":
            assert not any(key.startswith("primary_release_date") for key in mood.params), mood.key
            assert "with_runtime.gte" not in mood.params, mood.key
        else:
            assert not any(key.startswith("first_air_date") for key in mood.params), mood.key
