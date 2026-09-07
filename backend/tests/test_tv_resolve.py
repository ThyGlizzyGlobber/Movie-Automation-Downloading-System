from app.tmdb import TMDBClient
from app.tv_resolve import (
    aired_episode_numbers,
    episode_query,
    resolve_show,
    season_is_complete,
    season_pack_queries,
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


def test_resolve_show_drops_subtitle_for_a_variant():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {
        "id": 789,
        "name": "Star Trek: Discovery",
        "original_name": "Star Trek: Discovery",
    }

    identity = resolve_show(789, client)

    assert identity.variants == ["Star Trek: Discovery", "Star Trek"]


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
    assert captured["params"] == {"append_to_response": "credits"}


# ---------------------------------------------------------------------------
# Stage 13: pack query builders + aired-episode filtering (shared with
# worker.py's check_show).
# ---------------------------------------------------------------------------


def test_season_pack_queries_returns_two_shapes_zero_padded():
    assert season_pack_queries("Lanterns", 1) == ["Lanterns Season 01", "Lanterns S01 COMPLETE"]


def test_season_pack_queries_double_digit_season():
    assert season_pack_queries("Lanterns", 12) == ["Lanterns Season 12", "Lanterns S12 COMPLETE"]


def test_series_pack_query_builds_complete_series_suffix():
    assert series_pack_query("Lanterns") == "Lanterns complete series"


def test_aired_episode_numbers_excludes_unaired_and_missing_air_dates():
    episodes = [
        {"episode_number": 1, "air_date": "2026-08-16"},
        {"episode_number": 2, "air_date": "2026-08-23"},
        {"episode_number": 3, "air_date": "2099-01-01"},  # far future — unaired
        {"episode_number": 4, "air_date": None},  # no air date on record yet
    ]
    assert aired_episode_numbers(episodes, today="2026-09-07") == [1, 2]


def test_aired_episode_numbers_boundary_is_inclusive():
    episodes = [{"episode_number": 1, "air_date": "2026-09-07"}]
    assert aired_episode_numbers(episodes, today="2026-09-07") == [1]


def test_aired_episode_numbers_ignores_entries_with_no_episode_number():
    episodes = [{"episode_number": None, "air_date": "2026-08-16"}]
    assert aired_episode_numbers(episodes, today="2026-09-07") == []


def test_season_is_complete_true_when_every_episode_has_already_aired():
    episodes = [
        {"episode_number": 1, "air_date": "2026-08-16"},
        {"episode_number": 2, "air_date": "2026-08-23"},
    ]
    assert season_is_complete(episodes, today="2026-09-07") is True


def test_season_is_complete_false_with_an_unaired_episode():
    episodes = [
        {"episode_number": 1, "air_date": "2026-08-16"},
        {"episode_number": 2, "air_date": "2099-01-01"},
    ]
    assert season_is_complete(episodes, today="2026-09-07") is False


def test_season_is_complete_false_with_an_entirely_unscheduled_episode():
    episodes = [{"episode_number": 1, "air_date": "2026-08-16"}, {"episode_number": 2, "air_date": None}]
    assert season_is_complete(episodes, today="2026-09-07") is False


def test_season_is_complete_boundary_is_inclusive():
    episodes = [{"episode_number": 1, "air_date": "2026-09-07"}]
    assert season_is_complete(episodes, today="2026-09-07") is True


def test_season_is_complete_false_for_an_empty_season():
    assert season_is_complete([], today="2026-09-07") is False
