from datetime import datetime, timezone

import pytest
import requests

from app.tvmaze import TVMazeClient, season_airstamps

# The shape TVmaze actually returns, taken from a live call for Lanterns
# (api.tvmaze.com/shows/44776/episodes) so the parsing is tested against
# the real thing rather than a guess at it.
LANTERNS_EPISODES = [
    {"season": 1, "number": 5, "airdate": "2026-09-13", "airtime": "21:00", "airstamp": "2026-09-14T01:00:00+00:00"},
    {"season": 1, "number": 6, "airdate": "2026-09-20", "airtime": "21:00", "airstamp": "2026-09-21T01:00:00+00:00"},
    {"season": 2, "number": 1, "airdate": "2027-01-10", "airtime": "21:00", "airstamp": "2027-01-11T02:00:00+00:00"},
]


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data

    def json(self):
        return self._json


class FakeSession:
    """Records every call so a test can assert the lookup order, and can
    be told to fail a given path."""

    def __init__(self, routes: dict, raise_on: str | None = None):
        self.routes = routes
        self.raise_on = raise_on
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        path = url.replace("https://api.tvmaze.com", "")
        self.calls.append((path, params or {}))
        if self.raise_on and self.raise_on in path:
            raise requests.ConnectionError("tvmaze unreachable")
        for route, response in self.routes.items():
            if path.startswith(route):
                return response
        return FakeResponse(404, None)


def _client(routes, raise_on=None):
    session = FakeSession(routes, raise_on)
    return TVMazeClient(session=session), session


def test_airstamps_for_show_maps_a_tvdb_id_to_utc_release_times():
    client, session = _client(
        {
            "/lookup/shows": FakeResponse(json_data={"id": 44776, "name": "Lanterns"}),
            "/shows/44776/episodes": FakeResponse(json_data=LANTERNS_EPISODES),
        }
    )

    stamps = client.airstamps_for_show(tvdb_id=376098, imdb_id="tt26545992")

    assert stamps[(1, 6)] == datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)
    assert stamps[(2, 1)] == datetime(2027, 1, 11, 2, 0, tzinfo=timezone.utc)
    # TheTVDB first: it's the id TVmaze indexes most completely for TV.
    assert session.calls[0][1] == {"thetvdb": 376098}


def test_airstamps_for_show_falls_back_to_imdb_when_tvdb_is_unknown():
    client, session = _client(
        {
            "/lookup/shows?": FakeResponse(404),
            "/shows/44776/episodes": FakeResponse(json_data=LANTERNS_EPISODES),
        }
    )
    # 404 on both lookups -> no show, no episode call at all.
    assert client.airstamps_for_show(tvdb_id=1, imdb_id="tt1") == {}
    assert [c[1] for c in session.calls] == [{"thetvdb": 1}, {"imdb": "tt1"}]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tvdb_id": None, "imdb_id": None},  # TMDB had no external ids
    ],
)
def test_airstamps_for_show_makes_no_call_without_an_id(kwargs):
    client, session = _client({})
    assert client.airstamps_for_show(**kwargs) == {}
    assert session.calls == []


def test_airstamps_for_show_is_empty_when_tvmaze_is_unreachable():
    """The fallback that matters: this runs inside a scheduled check, so
    a network failure must mean "use TMDB's date", never an exception
    that stops a followed show updating."""
    client, _ = _client({"/lookup/shows": FakeResponse(json_data={"id": 44776})}, raise_on="/lookup/shows")

    assert client.airstamps_for_show(tvdb_id=376098, imdb_id=None) == {}


def test_airstamps_skips_episodes_with_no_usable_timestamp():
    """A schedule TVmaze hasn't timed yet (airstamp null, or a naive
    string) drops out rather than poisoning the season — those episodes
    fall back to the date rule individually."""
    client, _ = _client(
        {
            "/lookup/shows": FakeResponse(json_data={"id": 7}),
            "/shows/7/episodes": FakeResponse(
                json_data=[
                    {"season": 1, "number": 1, "airstamp": "2026-09-21T01:00:00+00:00"},
                    {"season": 1, "number": 2, "airstamp": None},
                    {"season": 1, "number": 3, "airstamp": "not a timestamp"},
                    {"season": 1, "number": 4, "airstamp": "2026-09-28T01:00:00"},  # no offset
                ]
            ),
        }
    )

    stamps = client.airstamps_for_show(tvdb_id=1, imdb_id=None)

    assert list(stamps) == [(1, 1)]


def test_season_airstamps_slices_one_season():
    whole_show = {
        (1, 1): datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
        (1, 2): datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
        (2, 1): datetime(2027, 1, 11, 2, 0, tzinfo=timezone.utc),
    }

    assert set(season_airstamps(whole_show, 1)) == {1, 2}
    assert set(season_airstamps(whole_show, 2)) == {1}
    assert season_airstamps(whole_show, 3) == {}
