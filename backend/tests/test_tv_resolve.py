from datetime import datetime, timedelta, timezone

from app.tmdb import TMDBClient
from app.tv_resolve import (
    aired_cutoff_date,
    episode_is_released,
    aired_episode_numbers,
    episode_placement_lookup,
    episode_query,
    resolve_show,
    season_is_complete,
    season_pack_queries,
    season_range_pack_queries,
    series_pack_queries,
    series_pack_query,
)


def test_episode_query_builds_s_e_token():
    assert episode_query("Lanterns", 1, 4) == "Lanterns S01E04"


def test_episode_query_zero_pads_single_digit_season_and_episode():
    assert episode_query("Lanterns", 1, 1) == "Lanterns S01E01"


def test_episode_query_handles_double_digit_season_and_episode():
    assert episode_query("Lanterns", 12, 34) == "Lanterns S12E34"


def test_resolve_show_uses_name_and_original_name():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {
        "id": 123,
        "name": "Lanterns",
        "original_name": "Lanterns",
    }

    identity = resolve_show(123, client)

    assert identity.tmdb_id == 123
    assert identity.title == "Lanterns"
    assert identity.original_title == "Lanterns"
    assert identity.variants == ["Lanterns"]


def test_resolve_show_includes_original_name_when_different():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {
        "id": 456,
        "name": "Attack on Titan",
        "original_name": "進撃の巨人",
    }

    identity = resolve_show(456, client)

    assert identity.variants == ["Attack on Titan", "進撃の巨人"]


def test_resolve_show_generates_both_sides_of_a_subtitle_split_as_variants():
    # Both "Star Trek" (head) and "Discovery" (tail) are real, commonly-used
    # ways to search for this show — generating only the head would have
    # missed "Discovery" entirely, the same gap that let a "Special Ops:
    # Lioness" search never try "Lioness" alone (confirmed live 2026-09-14).
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {
        "id": 789,
        "name": "Star Trek: Discovery",
        "original_name": "Star Trek: Discovery",
    }

    identity = resolve_show(789, client)

    assert identity.variants == ["Star Trek: Discovery", "Star Trek", "Discovery"]


def test_resolve_show_captures_first_air_year():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {
        "id": 95350,
        "name": "Lanterns",
        "original_name": "Lanterns",
        "first_air_date": "2026-08-16",
    }

    identity = resolve_show(95350, client)

    assert identity.first_air_year == 2026


def test_resolve_show_first_air_year_is_none_when_unset():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {"id": 1, "name": "Unreleased", "original_name": "Unreleased"}

    identity = resolve_show(1, client)

    assert identity.first_air_year is None


def test_resolve_show_requests_credits_append():
    client = TMDBClient(api_key="test-key")
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"id": 1, "name": "Lanterns", "original_name": "Lanterns"}

    client._get = fake_get
    resolve_show(1, client)

    assert captured["path"] == "/tv/1"
    assert captured["params"] == {
        "append_to_response": "credits,content_ratings,recommendations,images,external_ids",
        "include_image_language": "en,null",
    }


# ---------------------------------------------------------------------------
# Stage 13: pack query builders + aired-episode filtering (shared with
# worker.py's check_show).
# ---------------------------------------------------------------------------


def test_season_pack_queries_returns_the_literal_shapes_then_the_bare_title():
    assert season_pack_queries("Lanterns", 1) == [
        "Lanterns Season 01",
        "Lanterns S01 COMPLETE",
        "Lanterns Season 1",
        "Lanterns S01",
        "Lanterns",
    ]


def test_season_pack_queries_double_digit_season():
    assert season_pack_queries("Lanterns", 12) == [
        "Lanterns Season 12",
        "Lanterns S12 COMPLETE",
        "Lanterns Season 12",
        "Lanterns S12",
        "Lanterns",
    ]


def test_season_pack_queries_includes_a_bare_season_token_query():
    # Confirmed live 2026-09-14: a real, correctly-labeled 1080p season
    # pack for The Mentalist S01 was never surfaced by either of the two
    # original query shapes at all — only a bare "S01"-style query found
    # it. pack_score.py's gate still correctly rejects single-episode
    # results this broader query also picks up.
    assert "Lanterns S01" in season_pack_queries("Lanterns", 1)


def test_series_pack_query_builds_complete_series_suffix():
    assert series_pack_query("Lanterns") == "Lanterns complete series"


def test_series_pack_queries_widen_to_complete_then_the_bare_title():
    # Confirmed live 2026-09-16 (Batman: The Brave and the Bold): the
    # literal "complete series" query surfaced two dead torrents while the
    # real packs were named "Complete Seasons 1 to 3" / "S01-S03" — only a
    # bare title search found them. The gate does the filtering.
    assert series_pack_queries("Lanterns") == ["Lanterns complete series", "Lanterns complete", "Lanterns"]


def test_season_range_pack_queries_returns_four_shapes_zero_padded():
    assert season_range_pack_queries("Lanterns", 1, 3) == [
        "Lanterns S01-S03",
        "Lanterns Seasons 1-3",
        "Lanterns S1-S3",
        "Lanterns Season 1-3",
    ]


def test_season_range_pack_queries_double_digit_seasons():
    assert season_range_pack_queries("Lanterns", 10, 12) == [
        "Lanterns S10-S12",
        "Lanterns Seasons 10-12",
        "Lanterns S10-S12",
        "Lanterns Season 10-12",
    ]


def test_aired_episode_numbers_excludes_unaired_and_missing_air_dates():
    episodes = [
        {"episode_number": 1, "air_date": "2026-08-16"},
        {"episode_number": 2, "air_date": "2026-08-23"},
        {"episode_number": 3, "air_date": "2099-01-01"},  # far future — unaired
        {"episode_number": 4, "air_date": None},  # no air date on record yet
    ]
    assert aired_episode_numbers(episodes, now=datetime(2026, 9, 7)) == [1, 2]


def test_aired_episode_numbers_boundary_is_inclusive():
    episodes = [{"episode_number": 1, "air_date": "2026-09-07"}]
    assert aired_episode_numbers(episodes, now=datetime(2026, 9, 7)) == [1]


def test_aired_episode_numbers_ignores_entries_with_no_episode_number():
    episodes = [{"episode_number": None, "air_date": "2026-08-16"}]
    assert aired_episode_numbers(episodes, now=datetime(2026, 9, 7)) == []


def test_season_is_complete_true_when_every_episode_has_already_aired():
    episodes = [
        {"episode_number": 1, "air_date": "2026-08-16"},
        {"episode_number": 2, "air_date": "2026-08-23"},
    ]
    assert season_is_complete(episodes, now=datetime(2026, 9, 7)) is True


def test_season_is_complete_false_with_an_unaired_episode():
    episodes = [
        {"episode_number": 1, "air_date": "2026-08-16"},
        {"episode_number": 2, "air_date": "2099-01-01"},
    ]
    assert season_is_complete(episodes, now=datetime(2026, 9, 7)) is False


def test_season_is_complete_false_with_an_entirely_unscheduled_episode():
    episodes = [{"episode_number": 1, "air_date": "2026-08-16"}, {"episode_number": 2, "air_date": None}]
    assert season_is_complete(episodes, now=datetime(2026, 9, 7)) is False


def test_season_is_complete_boundary_is_inclusive():
    episodes = [{"episode_number": 1, "air_date": "2026-09-07"}]
    assert season_is_complete(episodes, now=datetime(2026, 9, 7)) is True


def test_season_is_complete_false_for_an_empty_season():
    assert season_is_complete([], now=datetime(2026, 9, 7)) is False


# ---------------------------------------------------------------------------
# episode_air_buffer_hours — a same-day air_date shouldn't count as aired
# until this many hours have passed since midnight on it (real-world case:
# a recheck firing the instant the calendar date rolled over, hours before
# the show's actual release and before any real torrent existed yet).
# ---------------------------------------------------------------------------


def test_aired_episode_numbers_same_day_air_date_not_yet_aired_within_buffer():
    episodes = [{"episode_number": 6, "air_date": "2026-09-08"}]
    # 03:00 on the air date itself, with a 12-hour buffer: only 3 hours
    # have passed since midnight, short of the buffer.
    assert aired_episode_numbers(episodes, now=datetime(2026, 9, 8, 3, 0), buffer_hours=12) == []


def test_aired_episode_numbers_same_day_air_date_aired_once_buffer_elapses():
    episodes = [{"episode_number": 6, "air_date": "2026-09-08"}]
    # 15:00 on the air date: 15 hours since midnight, past the 12-hour buffer.
    assert aired_episode_numbers(episodes, now=datetime(2026, 9, 8, 15, 0), buffer_hours=12) == [6]


def test_aired_episode_numbers_zero_buffer_matches_original_exact_date_behavior():
    episodes = [{"episode_number": 6, "air_date": "2026-09-08"}]
    assert aired_episode_numbers(episodes, now=datetime(2026, 9, 8, 0, 1), buffer_hours=0) == [6]


def test_season_is_complete_respects_buffer_on_its_last_episode():
    episodes = [
        {"episode_number": 1, "air_date": "2026-08-16"},
        {"episode_number": 2, "air_date": "2026-09-08"},
    ]
    assert season_is_complete(episodes, now=datetime(2026, 9, 8, 3, 0), buffer_hours=12) is False
    assert season_is_complete(episodes, now=datetime(2026, 9, 8, 15, 0), buffer_hours=12) is True


def test_resolve_show_carries_the_season_count_for_the_series_pack_gate():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {"id": 15804, "name": "Batman: The Brave and the Bold", "number_of_seasons": 3}

    assert resolve_show(15804, client).number_of_seasons == 3

    client._get = lambda path, params=None: {"id": 1, "name": "Lanterns"}
    assert resolve_show(1, client).number_of_seasons is None


def test_resolve_show_counts_finished_seasons_and_whether_the_show_has_ended():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {
        "id": 1,
        "name": "Reacher",
        "status": "Returning Series",
        "number_of_seasons": 4,
        "seasons": [
            {"season_number": 0, "air_date": "2022-01-01"},
            {"season_number": 1, "air_date": "2022-02-03"},
            {"season_number": 2, "air_date": "2023-12-15"},
            {"season_number": 3, "air_date": "2025-02-20"},
            {"season_number": 4, "air_date": "2026-08-12"},
        ],
        "next_episode_to_air": {"season_number": 4, "episode_number": 8},
    }
    identity = resolve_show(1, client)
    assert identity.number_of_seasons == 4
    assert identity.finished_seasons == 3
    assert identity.ended is False

    client._get = lambda path, params=None: {"id": 2, "name": "Batman", "status": "Ended", "number_of_seasons": 3}
    identity = resolve_show(2, client)
    assert identity.finished_seasons is None  # no season list from TMDB
    assert identity.ended is True


def test_finished_seasons_ignores_a_season_announced_for_the_future():
    from app.tv_resolve import _finished_seasons

    show = {
        "seasons": [{"season_number": 1, "air_date": "2020-01-01"}, {"season_number": 2, "air_date": "2999-01-01"}],
    }
    assert _finished_seasons(show, today="2026-09-17") == 1


def test_aired_cutoff_date_holds_an_episode_until_the_buffer_has_passed():
    """The air buffer's boundary, pinned against an explicit clock rather
    than whatever time the suite happens to run at.

    With 15 hours set, an episode dated 2026-09-21 is held through UTC
    midnight and released once 15:00 UTC passes — which is the whole
    point of the setting: the hours right after a date rolls over are
    when fake and empty releases get uploaded to catch tools searching
    too early.
    """
    air_date = "2026-09-21"
    midnight = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)

    def held_at(hour: float) -> bool:
        cutoff = aired_cutoff_date(midnight + timedelta(hours=hour), buffer_hours=15)
        return air_date > cutoff

    assert held_at(0) is True       # the instant the date rolls over
    assert held_at(14.9) is True    # still inside the buffer
    assert held_at(15) is False     # released
    assert held_at(23) is False


def test_aired_cutoff_date_with_no_buffer_releases_at_midnight():
    """0 means "search the instant the air_date arrives" — the behaviour
    the buffer was added to replace, still available."""
    midnight = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)

    assert aired_cutoff_date(midnight, buffer_hours=0) == "2026-09-21"
    assert aired_cutoff_date(midnight - timedelta(seconds=1), buffer_hours=0) == "2026-09-20"


# ---------------------------------------------------------------------------
# episode_is_released — the exact-timestamp path and its fallback.
# ---------------------------------------------------------------------------


def test_episode_is_released_measures_the_buffer_from_a_real_airstamp():
    """Lanterns S01E06, the case this was built for. TMDB dates it
    2026-09-20; HBO actually released it at 2026-09-21T01:00Z (21:00
    America/New_York), which TVmaze publishes as `airstamp`.

    Anchored to the airstamp, a 15-hour buffer finally means fifteen
    hours after it was genuinely out."""
    airstamp = datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)

    def released_at(when: datetime) -> bool:
        return episode_is_released("2026-09-20", airstamp, now=when, buffer_hours=15)

    assert released_at(datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)) is False  # the old anchor's expiry
    assert released_at(datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)) is False   # the moment it aired
    assert released_at(datetime(2026, 9, 21, 15, 59, tzinfo=timezone.utc)) is False
    assert released_at(datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)) is True   # airstamp + 15h


def test_episode_is_released_shows_what_the_date_anchor_got_wrong():
    """The same episode, same buffer, with no airstamp: the date rule
    calls it ready at 15:00 on the 20th — ten hours before HBO put it
    out. This is the behaviour TVmaze replaces, kept as the fallback
    because it is still better than no delay for a show TVmaze doesn't
    know."""
    assert episode_is_released(
        "2026-09-20", None, now=datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), buffer_hours=15
    ) is True


def test_episode_is_released_falls_back_when_the_airstamp_is_missing():
    """A show TVmaze has, with one episode it hasn't timestamped yet."""
    early = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert episode_is_released("2026-09-20", None, now=early, buffer_hours=15) is False
    assert episode_is_released("2026-09-18", None, now=early, buffer_hours=15) is True


def test_aired_episode_numbers_prefers_airstamps_per_episode():
    """A season part-covered by TVmaze: each episode takes whichever
    rule it has data for."""
    episodes = [
        {"episode_number": 1, "air_date": "2026-09-13"},
        {"episode_number": 2, "air_date": "2026-09-20"},  # stamped, still held
        {"episode_number": 3, "air_date": "2026-09-20"},  # unstamped, date rule releases it
    ]
    now = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
    airstamps = {2: datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)}

    assert aired_episode_numbers(episodes, now=now, buffer_hours=15, airstamps=airstamps) == [1, 3]


def test_season_is_complete_uses_airstamps_too():
    """Otherwise a season whose finale is still inside its buffer would
    read as finished and get requested as a pack."""
    episodes = [
        {"episode_number": 1, "air_date": "2026-09-13"},
        {"episode_number": 2, "air_date": "2026-09-20"},
    ]
    now = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
    airstamps = {2: datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)}

    assert season_is_complete(episodes, now=now, buffer_hours=15, airstamps=airstamps) is False
    assert season_is_complete(episodes, now=now, buffer_hours=15) is True  # date rule alone: wrongly "done"


# ---------------------------------------------------------------------------
# episode_placement_lookup — packs number specials as extra episodes at
# the end of a season; TMDB keeps them in season 0.
# ---------------------------------------------------------------------------


class _FakeSeasons:
    """Only get_tv_season is used. Aired dates are in the past unless a
    season is given an unaired episode explicitly."""

    def __init__(self, seasons, fail_on=()):
        self._seasons = seasons
        self._fail_on = set(fail_on)
        self.calls: list[int] = []

    def get_tv_season(self, tmdb_id, season_number):
        self.calls.append(season_number)
        if season_number in self._fail_on:
            raise RuntimeError("tmdb down")
        return self._seasons.get(season_number, [])


def _eps(names, air_date="2020-01-01"):
    return [
        {"episode_number": i, "name": n, "air_date": air_date}
        for i, n in enumerate(names, start=1)
    ]


def test_placement_leaves_a_real_episode_alone():
    tmdb = _FakeSeasons({1: _eps(["One", "Two"])})
    assert episode_placement_lookup(7, tmdb)(1, 2) == (1, 2, "Two")


def test_placement_moves_an_appended_special_into_season_zero():
    tmdb = _FakeSeasons({0: _eps(["The Special"]), 1: _eps(["One", "Two"])})
    assert episode_placement_lookup(7, tmdb)(1, 3) == (0, 1, "The Special")


def test_placement_treats_every_extra_as_a_special_even_untitled():
    """A pack with more files than the season has episodes is carrying
    extras whatever TMDB lists, so the second one goes to Season 00 too
    — untitled rather than left as a phantom episode."""
    tmdb = _FakeSeasons({0: _eps(["The Special"]), 1: _eps(["One", "Two"])})
    place = episode_placement_lookup(7, tmdb)
    place(1, 3)  # claims the one special TMDB lists
    assert place(1, 4) == (0, 2, None)


def test_placement_never_gives_two_files_the_same_special():
    """Two extras against one listed special must not build one path —
    the second file would replace the first and vanish out of a pack
    that organized "successfully"."""
    tmdb = _FakeSeasons({0: _eps(["The Special"]), 1: _eps(["One", "Two"])})
    place = episode_placement_lookup(7, tmdb)
    first = place(1, 3, "Show.S01E03")
    second = place(1, 4, "Show.S01E04")
    assert first[:2] != second[:2]


def test_placement_picks_the_special_named_in_the_filename():
    tmdb = _FakeSeasons(
        {
            0: [
                {"episode_number": 1, "name": "Behind the Scenes", "air_date": "2020-06-01", "runtime": 10},
                {"episode_number": 2, "name": "The Winter Ball", "air_date": "2020-06-02", "runtime": 60},
            ],
            1: _eps(["One", "Two"]),
        }
    )
    place = episode_placement_lookup(7, tmdb, "Show")
    assert place(1, 3, "Show.S01E03.The.Winter.Ball.1080p") == (0, 2, "The Winter Ball")


def test_placement_ignores_the_shows_own_name_when_matching():
    """Every filename leads with the show's title and plenty of specials
    repeat it, so counting those words ties everything against
    everything — the real Doctor Who case."""
    tmdb = _FakeSeasons(
        {
            0: [
                {"episode_number": 1, "name": "Doctor Who at the Proms", "air_date": "2020-06-01", "runtime": 7},
                {"episode_number": 2, "name": "The Next Doctor", "air_date": "2020-06-02", "runtime": 60},
            ],
            1: _eps(["One", "Two"]),
        }
    )
    place = episode_placement_lookup(7, tmdb, "Doctor Who")
    assert place(1, 3, "Doctor.Who.S01E03.The.Next.Doctor.1080p") == (0, 2, "The Next Doctor")


def test_placement_falls_back_to_runtime_when_the_filename_says_nothing():
    tmdb = _FakeSeasons(
        {
            0: [
                {"episode_number": 1, "name": "Short One", "air_date": "2020-06-01", "runtime": 7},
                {"episode_number": 2, "name": "Long One", "air_date": "2020-06-02", "runtime": 60},
            ],
            1: _eps(["One", "Two"]),
        }
    )
    place = episode_placement_lookup(7, tmdb, "Show")
    assert place(1, 3, "Show.S01E03.1080p", lambda: 58.0) == (0, 2, "Long One")
    assert place(1, 4, "Show.S01E04.1080p", lambda: 7.5) == (0, 1, "Short One")


def test_placement_ignores_a_runtime_nowhere_near_either_candidate():
    """Out of tolerance stops being a tiebreak rather than rejecting a
    candidate, so this falls through to position."""
    tmdb = _FakeSeasons(
        {
            0: [
                {"episode_number": 1, "name": "Short One", "air_date": "2020-06-01", "runtime": 7},
                {"episode_number": 2, "name": "Long One", "air_date": "2020-06-02", "runtime": 60},
            ],
            1: _eps(["One", "Two"]),
        }
    )
    place = episode_placement_lookup(7, tmdb, "Show")
    assert place(1, 3, "Show.S01E03.1080p", lambda: 200.0) == (0, 1, "Short One")


def test_placement_windows_specials_to_the_season_they_belong_to():
    """A special that aired during season 2 is not a candidate for a
    season 1 pack — the narrowing that makes everything after it
    tractable."""
    tmdb = _FakeSeasons(
        {
            0: [
                {"episode_number": 1, "name": "S1 Extra", "air_date": "2020-03-01", "runtime": 30},
                {"episode_number": 2, "name": "S2 Extra", "air_date": "2022-03-01", "runtime": 30},
            ],
            1: _eps(["One", "Two"], air_date="2020-01-01"),
            2: _eps(["One", "Two"], air_date="2022-01-01"),
        }
    )
    place = episode_placement_lookup(7, tmdb, "Show")
    assert place(1, 3, "Show.S01E03.1080p") == (0, 1, "S1 Extra")


def test_placement_leaves_season_zero_numbering_untouched():
    tmdb = _FakeSeasons({0: _eps(["The Special"])})
    assert episode_placement_lookup(7, tmdb)(0, 1) == (0, 1, "The Special")


def test_placement_refuses_while_the_season_is_still_airing():
    """The guard that matters: on an airing season TMDB's count is a
    moving target, and a same-day release of the next episode would
    otherwise be filed as a special."""
    airing = _eps(["One", "Two"]) + [{"episode_number": 3, "name": "Three", "air_date": None}]
    tmdb = _FakeSeasons({0: _eps(["The Special"]), 1: airing})
    assert episode_placement_lookup(7, tmdb)(1, 4) == (1, 4, None)


def test_placement_refuses_when_tmdb_cannot_answer():
    """An outage caches as an empty season; that must not read as
    "every episode is out of range"."""
    tmdb = _FakeSeasons({0: _eps(["The Special"])}, fail_on=(1,))
    assert episode_placement_lookup(7, tmdb)(1, 9) == (1, 9, None)


def test_placement_fetches_each_season_at_most_once():
    tmdb = _FakeSeasons({0: _eps(["The Special"]), 1: _eps(["One", "Two"])})
    place = episode_placement_lookup(7, tmdb)
    for episode in (1, 2, 3, 4):
        place(1, episode)
    # Season 2 as well now: the window needs to know where season 1
    # stops and the next one starts.
    assert sorted(set(tmdb.calls)) == [0, 1, 2]
    assert tmdb.calls.count(1) == 1
