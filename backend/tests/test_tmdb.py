from datetime import datetime, timedelta, timezone

from app.tmdb import (
    TMDBClient,
    _available_on_provider,
    _is_recent_release,
    _lacks_digital_release,
    best_trailer_key,
    is_movie_coming_soon,
    is_tv_upcoming,
)


def test_is_recent_release_true_for_a_title_released_this_month():
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    assert _is_recent_release({"release_date": recent}) is True


def test_is_recent_release_false_for_a_decades_old_release_date():
    assert _is_recent_release({"release_date": "1998-10-16"}) is False


def test_is_recent_release_false_when_release_date_missing():
    assert _is_recent_release({}) is False


def _release_dates(country_entries):
    return country_entries


def test_lacks_digital_release_true_when_only_theatrical():
    releases = _release_dates([
        {"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-07-30T00:00:00.000Z"}]},
    ])
    assert _lacks_digital_release(releases, "US") is True


def test_lacks_digital_release_false_once_digital_date_has_passed():
    releases = _release_dates([
        {
            "iso_3166_1": "US",
            "release_dates": [
                {"type": 3, "release_date": "2026-07-30T00:00:00.000Z"},
                {"type": 4, "release_date": "2020-01-01T00:00:00.000Z"},
            ],
        },
    ])
    assert _lacks_digital_release(releases, "US") is False


def test_lacks_digital_release_true_when_digital_date_is_in_the_future():
    releases = _release_dates([
        {
            "iso_3166_1": "US",
            "release_dates": [
                {"type": 3, "release_date": "2026-07-30T00:00:00.000Z"},
                {"type": 4, "release_date": "2099-01-01T00:00:00.000Z"},
            ],
        },
    ])
    assert _lacks_digital_release(releases, "US") is True


def test_lacks_digital_release_true_when_region_absent_entirely():
    releases = _release_dates([
        {"iso_3166_1": "FR", "release_dates": [{"type": 4, "release_date": "2020-01-01T00:00:00.000Z"}]},
    ])
    assert _lacks_digital_release(releases, "US") is True


def test_lacks_digital_release_physical_type_counts_too():
    releases = _release_dates([
        {"iso_3166_1": "US", "release_dates": [{"type": 5, "release_date": "2020-01-01T00:00:00.000Z"}]},
    ])
    assert _lacks_digital_release(releases, "US") is False


def test_get_coming_soon_filters_out_titles_with_a_past_digital_release(monkeypatch):
    client = TMDBClient(api_key="test-key")
    now_playing_response = {
        "page": 1,
        "total_pages": 1,
        "results": [
            {"id": 1, "title": "Still In Theaters", "release_date": "2026-08-15"},
            {"id": 2, "title": "Already Streaming", "release_date": "2026-08-01"},
            # A 1998 catalog title on a 2026 anniversary theatrical re-release
            # (real case: "Practical Magic") — TMDB's now_playing surfaces
            # it, but its own `release_date` field is still the original
            # 1998 date, and TMDB has no US Digital/Physical entry for it at
            # all. Must be excluded by the recency check alone, without ever
            # calling release_dates for it (asserted below).
            {"id": 3, "title": "Old Re-Release", "release_date": "1998-10-16"},
        ],
    }
    release_dates_by_id = {
        1: {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-07-30T00:00:00.000Z"}]}]},
        2: {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 3, "release_date": "2026-01-01T00:00:00.000Z"},
                        {"type": 4, "release_date": "2026-02-01T00:00:00.000Z"},
                    ],
                }
            ]
        },
    }
    fetched_release_dates_for: list[int] = []

    def fake_get(path, params=None):
        if path == "/movie/now_playing":
            return now_playing_response
        movie_id = int(path.split("/")[2])
        fetched_release_dates_for.append(movie_id)
        return release_dates_by_id[movie_id]

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_coming_soon(region="US", page=1)

    assert [m["title"] for m in result["results"]] == ["Still In Theaters"]
    assert result["page"] == 1
    assert 3 not in fetched_release_dates_for


def test_get_available_popular_excludes_theatrical_only_titles(monkeypatch):
    client = TMDBClient(api_key="test-key")
    popular_response = {
        "page": 1,
        "total_pages": 100,
        "results": [
            {"id": 1, "title": "Out On Digital"},
            {"id": 2, "title": "Still In Theaters Only"},
        ],
    }
    release_dates_by_id = {
        1: {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 3, "release_date": "2026-01-01T00:00:00.000Z"},
                        {"type": 4, "release_date": "2026-02-01T00:00:00.000Z"},
                    ],
                }
            ]
        },
        2: {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-08-15T00:00:00.000Z"}]}]},
    }

    def fake_get(path, params=None):
        if path == "/movie/popular":
            return popular_response
        movie_id = int(path.split("/")[2])
        return release_dates_by_id[movie_id]

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_available_popular(page=1, region="US")

    assert [m["title"] for m in result["results"]] == ["Out On Digital"]
    assert result["total_pages"] == 100


def test_get_available_trending_excludes_theatrical_only_titles(monkeypatch):
    client = TMDBClient(api_key="test-key")
    trending_response = {
        "page": 1,
        "results": [
            {"id": 1, "title": "Out On Digital"},
            {"id": 2, "title": "Still In Theaters Only"},
        ],
    }
    release_dates_by_id = {
        1: {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 3, "release_date": "2026-01-01T00:00:00.000Z"},
                        {"type": 5, "release_date": "2026-02-01T00:00:00.000Z"},
                    ],
                }
            ]
        },
        2: {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-08-15T00:00:00.000Z"}]}]},
    }

    def fake_get(path, params=None):
        if path == "/trending/movie/week":
            return trending_response
        movie_id = int(path.split("/")[2])
        return release_dates_by_id[movie_id]

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_available_trending(time_window="week", region="US")

    assert [m["title"] for m in result["results"]] == ["Out On Digital"]


def test_get_available_by_genre_excludes_theatrical_only_titles(monkeypatch):
    client = TMDBClient(api_key="test-key")
    discover_response = {
        "page": 1,
        "total_pages": 10,
        "results": [
            {"id": 1, "title": "Out On Digital"},
            {"id": 2, "title": "Still In Theaters Only"},
        ],
    }
    release_dates_by_id = {
        1: {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 4, "release_date": "2026-02-01T00:00:00.000Z"}]}]},
        2: {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-08-15T00:00:00.000Z"}]}]},
    }

    def fake_get(path, params=None):
        if path == "/discover/movie":
            return discover_response
        movie_id = int(path.split("/")[2])
        return release_dates_by_id[movie_id]

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_available_by_genre(28, region="US")

    assert [m["title"] for m in result["results"]] == ["Out On Digital"]
    assert result["total_pages"] == 10


def test_get_available_by_provider_excludes_theatrical_only_titles(monkeypatch):
    client = TMDBClient(api_key="test-key")
    discover_response = {
        "page": 1,
        "total_pages": 10,
        "results": [
            {"id": 1, "title": "Out On Digital"},
            {"id": 2, "title": "Still In Theaters Only"},
        ],
    }
    release_dates_by_id = {
        1: {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 4, "release_date": "2026-02-01T00:00:00.000Z"}]}]},
        2: {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-08-15T00:00:00.000Z"}]}]},
    }

    def fake_get(path, params=None):
        if path == "/discover/movie":
            return discover_response
        movie_id = int(path.split("/")[2])
        return release_dates_by_id[movie_id]

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_available_by_provider(8, region="US")

    assert [m["title"] for m in result["results"]] == ["Out On Digital"]


def test_is_movie_coming_soon_true_for_recent_theatrical_only_release():
    movie = {"release_date": (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")}
    releases = [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": "2026-01-01T00:00:00.000Z"}]}]
    assert is_movie_coming_soon(movie, releases, "US") is True


def test_is_movie_coming_soon_false_once_digitally_released():
    movie = {"release_date": (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")}
    releases = [{"iso_3166_1": "US", "release_dates": [{"type": 4, "release_date": "2020-01-01T00:00:00.000Z"}]}]
    assert is_movie_coming_soon(movie, releases, "US") is False


def test_is_movie_coming_soon_false_for_an_old_title_with_no_digital_record():
    # Same "Practical Magic" gap as get_coming_soon's own recency guard —
    # an old title TMDB never logged a Digital/Physical entry for must not
    # read as permanently Coming Soon.
    movie = {"release_date": "1998-10-16"}
    assert is_movie_coming_soon(movie, [], "US") is False


def test_is_tv_upcoming_true_for_a_future_first_air_date():
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    assert is_tv_upcoming({"first_air_date": future}) is True


def test_is_tv_upcoming_true_when_first_air_date_missing():
    assert is_tv_upcoming({}) is True


def test_is_tv_upcoming_false_once_a_show_has_aired():
    past = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    assert is_tv_upcoming({"first_air_date": past}) is False


def test_get_available_tv_popular_excludes_unaired_shows(monkeypatch):
    client = TMDBClient(api_key="test-key")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    past = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    popular_response = {
        "page": 1,
        "total_pages": 500,
        "results": [
            {"id": 1, "name": "Already Airing", "first_air_date": past},
            {"id": 2, "name": "Not Yet Aired", "first_air_date": future},
        ],
    }

    def fake_get(path, params=None):
        assert path == "/tv/popular"
        return popular_response

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_available_tv_popular(page=1)

    assert [s["name"] for s in result["results"]] == ["Already Airing"]


def test_get_available_tv_by_genre_excludes_unaired_shows(monkeypatch):
    client = TMDBClient(api_key="test-key")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    past = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    discover_response = {
        "page": 1,
        "total_pages": 10,
        "results": [
            {"id": 1, "name": "Already Airing", "first_air_date": past},
            {"id": 2, "name": "Not Yet Aired", "first_air_date": future},
        ],
    }

    def fake_get(path, params=None):
        assert path == "/discover/tv"
        return discover_response

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_available_tv_by_genre(35, region="US")

    assert [s["name"] for s in result["results"]] == ["Already Airing"]


def test_get_tv_coming_soon_keeps_only_unaired_shows(monkeypatch):
    client = TMDBClient(api_key="test-key")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    discover_response = {
        "page": 1,
        "total_pages": 3,
        "results": [
            {"id": 1, "name": "Airing Right On The Boundary", "first_air_date": today},
            {"id": 2, "name": "Airing Later", "first_air_date": future},
        ],
    }
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return discover_response

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_tv_coming_soon(region="US", page=1)

    assert captured["path"] == "/discover/tv"
    assert captured["params"]["first_air_date.gte"] == today
    # "today" itself isn't strictly in the future, so is_tv_upcoming's own
    # re-check (not just trusting TMDB's date filter) correctly drops it.
    assert [s["name"] for s in result["results"]] == ["Airing Later"]


def test_available_on_provider_matches_any_offer_kind():
    watch_providers = {"US": {"rent": [{"provider_id": 8}], "flatrate": [{"provider_id": 337}]}}
    assert _available_on_provider(watch_providers, "US", 8) is True
    assert _available_on_provider(watch_providers, "US", 337) is True


def test_available_on_provider_false_when_provider_absent():
    watch_providers = {"US": {"flatrate": [{"provider_id": 337}]}}
    assert _available_on_provider(watch_providers, "US", 8) is False


def test_available_on_provider_false_when_region_absent():
    watch_providers = {"FR": {"flatrate": [{"provider_id": 8}]}}
    assert _available_on_provider(watch_providers, "US", 8) is False


def test_search_tv_passes_first_air_date_year_when_given():
    client = TMDBClient(api_key="test-key")
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"results": []}

    client._get = fake_get
    client.search_tv("Lanterns", year=2026)

    assert captured["path"] == "/search/tv"
    assert captured["params"] == {"query": "Lanterns", "first_air_date_year": 2026}


def test_search_tv_omits_year_param_when_not_given():
    client = TMDBClient(api_key="test-key")
    captured = {}

    def fake_get(path, params=None):
        captured["params"] = params
        return {"results": []}

    client._get = fake_get
    client.search_tv("Lanterns")

    assert captured["params"] == {"query": "Lanterns"}


def test_get_tv_requests_credits_and_content_ratings_append():
    client = TMDBClient(api_key="test-key")
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"id": 1, "name": "Lanterns"}

    client._get = fake_get
    result = client.get_tv(1)

    assert captured["path"] == "/tv/1"
    assert captured["params"] == {"append_to_response": "credits,content_ratings,recommendations"}
    assert result["name"] == "Lanterns"


# ---------------------------------------------------------------------------
# best_trailer_key — preference order: official Trailer, any Trailer,
# official Teaser, any Teaser, then None.
# ---------------------------------------------------------------------------


def test_best_trailer_key_prefers_official_trailer_over_everything_else():
    videos = [
        {"site": "YouTube", "type": "Teaser", "official": True, "key": "teaser-key"},
        {"site": "YouTube", "type": "Trailer", "official": False, "key": "unofficial-trailer-key"},
        {"site": "YouTube", "type": "Trailer", "official": True, "key": "official-trailer-key"},
    ]
    assert best_trailer_key(videos) == "official-trailer-key"


def test_best_trailer_key_falls_back_to_unofficial_trailer():
    videos = [{"site": "YouTube", "type": "Trailer", "official": False, "key": "unofficial-trailer-key"}]
    assert best_trailer_key(videos) == "unofficial-trailer-key"


def test_best_trailer_key_falls_back_to_teaser_when_no_trailer_exists():
    videos = [{"site": "YouTube", "type": "Teaser", "official": True, "key": "teaser-key"}]
    assert best_trailer_key(videos) == "teaser-key"


def test_best_trailer_key_ignores_non_youtube_and_non_trailer_video_types():
    videos = [
        {"site": "Vimeo", "type": "Trailer", "official": True, "key": "vimeo-key"},
        {"site": "YouTube", "type": "Featurette", "official": True, "key": "featurette-key"},
        {"site": "YouTube", "type": "Bloopers", "official": True, "key": "bloopers-key"},
    ]
    assert best_trailer_key(videos) is None


def test_best_trailer_key_returns_none_for_no_videos():
    assert best_trailer_key([]) is None


def test_get_movie_videos_returns_results_list():
    client = TMDBClient(api_key="test-key")

    def fake_get(path, params=None):
        assert path == "/movie/1/videos"
        return {"results": [{"key": "abc", "site": "YouTube", "type": "Trailer"}]}

    client._get = fake_get
    assert client.get_movie_videos(1) == [{"key": "abc", "site": "YouTube", "type": "Trailer"}]


def test_get_movie_videos_missing_results_key_returns_empty_list():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {}
    assert client.get_movie_videos(1) == []


def test_get_tv_videos_returns_results_list():
    client = TMDBClient(api_key="test-key")

    def fake_get(path, params=None):
        assert path == "/tv/1/videos"
        return {"results": [{"key": "xyz", "site": "YouTube", "type": "Teaser"}]}

    client._get = fake_get
    assert client.get_tv_videos(1) == [{"key": "xyz", "site": "YouTube", "type": "Teaser"}]


def test_get_person_requests_combined_credits_append():
    client = TMDBClient(api_key="test-key")
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"id": 1, "name": "Jason Sudeikis"}

    client._get = fake_get
    result = client.get_person(1)

    assert captured["path"] == "/person/1"
    assert captured["params"] == {"append_to_response": "combined_credits"}
    assert result["name"] == "Jason Sudeikis"


def test_get_tv_season_returns_episode_list():
    client = TMDBClient(api_key="test-key")

    def fake_get(path, params=None):
        assert path == "/tv/1/season/1"
        return {"episodes": [{"episode_number": 1, "air_date": "2026-01-01"}, {"episode_number": 2, "air_date": "2026-01-08"}]}

    client._get = fake_get
    episodes = client.get_tv_season(1, 1)

    assert [e["episode_number"] for e in episodes] == [1, 2]


def test_get_tv_season_missing_episodes_key_returns_empty_list():
    client = TMDBClient(api_key="test-key")
    client._get = lambda path, params=None: {}

    assert client.get_tv_season(1, 1) == []


def test_get_tv_popular_is_ttl_cached():
    client = TMDBClient(api_key="test-key")
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        return {"results": [{"id": 1, "name": "Popular Show"}]}

    client._get = fake_get
    client.get_tv_popular()
    client.get_tv_popular()

    assert calls == ["/tv/popular"]


def test_get_tv_trending_is_ttl_cached():
    client = TMDBClient(api_key="test-key")
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        return {"results": [{"id": 1, "name": "Trending Show"}]}

    client._get = fake_get
    client.get_tv_trending()
    client.get_tv_trending()

    assert calls == ["/trending/tv/week"]


def test_search_within_provider_filters_to_titles_on_that_service(monkeypatch):
    client = TMDBClient(api_key="test-key")
    search_response = {
        "results": [
            {"id": 1, "title": "On Netflix"},
            {"id": 2, "title": "On Disney Plus Only"},
        ]
    }
    watch_providers_by_id = {
        1: {"results": {"US": {"flatrate": [{"provider_id": 8}]}}},
        2: {"results": {"US": {"flatrate": [{"provider_id": 337}]}}},
    }

    def fake_get(path, params=None):
        if path == "/search/movie":
            return search_response
        movie_id = int(path.split("/")[2])
        return watch_providers_by_id[movie_id]

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.search_within_provider("some query", provider_id=8, region="US")

    assert [m["title"] for m in result["results"]] == ["On Netflix"]


def test_discover_tv_by_provider_passes_provider_and_region_through():
    client = TMDBClient(api_key="test-key")
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        return {"results": [{"id": 1, "name": "Some Show"}]}

    client._get = fake_get
    client.discover_tv_by_provider(8, region="GB", page=2)

    assert calls == [("/discover/tv", {"with_watch_providers": 8, "watch_region": "GB", "page": 2, "sort_by": "popularity.desc"})]


def test_discover_by_genre_passes_genre_and_region_through():
    client = TMDBClient(api_key="test-key")
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        return {"results": [{"id": 1, "title": "Some Movie"}]}

    client._get = fake_get
    client.discover_by_genre(28, region="GB", page=2)

    assert calls == [("/discover/movie", {"with_genres": 28, "region": "GB", "page": 2, "sort_by": "popularity.desc"})]


def test_discover_by_genre_is_ttl_cached():
    client = TMDBClient(api_key="test-key")
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        return {"results": [{"id": 1, "title": "Some Movie"}]}

    client._get = fake_get
    client.discover_by_genre(28)
    client.discover_by_genre(28)

    assert calls == ["/discover/movie"]


def test_discover_tv_by_genre_passes_genre_and_region_through():
    client = TMDBClient(api_key="test-key")
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        return {"results": [{"id": 1, "name": "Some Show"}]}

    client._get = fake_get
    client.discover_tv_by_genre(35, region="GB", page=2)

    assert calls == [("/discover/tv", {"with_genres": 35, "region": "GB", "page": 2, "sort_by": "popularity.desc"})]


def test_discover_tv_by_genre_is_ttl_cached():
    client = TMDBClient(api_key="test-key")
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        return {"results": [{"id": 1, "name": "Some Show"}]}

    client._get = fake_get
    client.discover_tv_by_genre(35)
    client.discover_tv_by_genre(35)

    assert calls == ["/discover/tv"]


def test_search_tv_within_provider_filters_to_shows_on_that_service(monkeypatch):
    client = TMDBClient(api_key="test-key")
    search_response = {
        "results": [
            {"id": 1, "name": "On Netflix"},
            {"id": 2, "name": "On Disney Plus Only"},
        ]
    }
    watch_providers_by_id = {
        1: {"results": {"US": {"flatrate": [{"provider_id": 8}]}}},
        2: {"results": {"US": {"flatrate": [{"provider_id": 337}]}}},
    }

    def fake_get(path, params=None):
        if path == "/search/tv":
            return search_response
        show_id = int(path.split("/")[2])
        return watch_providers_by_id[show_id]

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.search_tv_within_provider("some query", provider_id=8, region="US")

    assert [s["name"] for s in result["results"]] == ["On Netflix"]
