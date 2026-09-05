from app.tmdb import TMDBClient
from app.tv_resolve import episode_query, resolve_show


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
