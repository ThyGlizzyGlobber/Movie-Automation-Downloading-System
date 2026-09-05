"""TMDB client wrapper. Called only from the backend — the key never
reaches the browser; the frontend hotlinks TMDB's public image CDN
directly instead of proxying images."""

from datetime import datetime, timezone

import requests

from app.cache import ttl_cache

BASE_URL = "https://api.themoviedb.org/3"
POPULAR_DISCOVER_TTL_SECONDS = 300

# TMDB's /movie/{id}/release_dates `type` field: 1 Premiere, 2 Theatrical
# (limited), 3 Theatrical, 4 Digital, 5 Physical, 6 TV.
_DIGITAL_RELEASE_TYPE = 4
_PHYSICAL_RELEASE_TYPE = 5

# /movie/now_playing can surface an old catalog title on a theatrical
# re-release (its `dates` window follows the re-release date, but each
# result's own `release_date` field is still the *original* release) —
# confirmed live with "Practical Magic" (1998, US anniversary re-release
# 2026): TMDB has no Digital/Physical entry on record for it at all, so
# `_lacks_digital_release` alone let a 1998 title through as "Coming Soon".
# This is a free, no-extra-request check (unlike the release_dates lookup)
# run first as a short-circuit.
_MAX_COMING_SOON_AGE_DAYS = 400

# /movie/{id}/watch/providers offer buckets — any of them counts as "on this
# service" for the provider-scoped search, matching discover_by_provider's
# own with_watch_providers (which doesn't restrict monetization type either).
_OFFER_KINDS = ("flatrate", "free", "ads", "rent", "buy")


class TMDBError(RuntimeError):
    pass


def _available_on_provider(watch_providers_by_region: dict, region: str, provider_id: int) -> bool:
    region_data = watch_providers_by_region.get(region)
    if not region_data:
        return False
    return any(
        offer.get("provider_id") == provider_id
        for kind in _OFFER_KINDS
        for offer in region_data.get(kind, [])
    )


def _is_recent_release(movie: dict, max_age_days: int = _MAX_COMING_SOON_AGE_DAYS) -> bool:
    release_date = movie.get("release_date")
    if not release_date:
        return False
    released_at = datetime.fromisoformat(release_date).replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - released_at).days <= max_age_days


def _lacks_digital_release(release_dates_by_country: list[dict], region: str) -> bool:
    """True if `region`'s release-dates entry has no Digital/Physical
    release dated today or earlier — i.e. still theatrical-only there. A
    region with no entry at all is treated the same way: TMDB simply has
    nothing on record yet, which isn't evidence of a digital release."""
    now = datetime.now(timezone.utc)
    for country in release_dates_by_country:
        if country.get("iso_3166_1") != region:
            continue
        for rd in country.get("release_dates", []):
            if rd.get("type") not in (_DIGITAL_RELEASE_TYPE, _PHYSICAL_RELEASE_TYPE):
                continue
            release_date = rd.get("release_date")
            if not release_date:
                continue
            released_at = datetime.fromisoformat(release_date.replace("Z", "+00:00"))
            if released_at <= now:
                return False
        return True
    return True


class TMDBClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise TMDBError("TMDB API key is not configured")
        self.api_key = api_key
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["api_key"] = self.api_key
        response = self.session.get(f"{BASE_URL}{path}", params=params, timeout=10)
        if not response.ok:
            raise TMDBError(f"TMDB {path} failed: {response.status_code} {response.text[:200]}")
        return response.json()

    # -- per-query lookups (not cached: each call is for a distinct title) --

    def search_movie(self, query: str, year: int | None = None) -> dict:
        params = {"query": query}
        if year:
            params["year"] = year
        return self._get("/search/movie", params)

    def get_movie(self, tmdb_id: int) -> dict:
        # append_to_response folds cast/crew and per-region certifications
        # into the one call the detail view needs (Stage 4 addendum: cast,
        # crew, and a certification badge on the movie detail page).
        return self._get(f"/movie/{tmdb_id}", {"append_to_response": "credits,release_dates"})

    def get_alternative_titles(self, tmdb_id: int) -> list[dict]:
        data = self._get(f"/movie/{tmdb_id}/alternative_titles")
        return data.get("titles", [])

    # -- browse surface: TTL-cached, since these are repeatedly hit by the
    #    home grid and provider rows rather than being per-title lookups --

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_popular(self, page: int = 1) -> dict:
        return self._get("/movie/popular", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_trending(self, time_window: str = "week") -> dict:
        return self._get(f"/trending/movie/{time_window}")

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_watch_providers(self, region: str = "US") -> dict:
        return self._get("/watch/providers/movie", {"watch_region": region})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def discover_by_provider(self, provider_id: int, region: str = "US", page: int = 1) -> dict:
        return self._get(
            "/discover/movie",
            {
                "with_watch_providers": provider_id,
                "watch_region": region,
                "page": page,
                "sort_by": "popularity.desc",
            },
        )

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_now_playing(self, region: str = "US", page: int = 1) -> dict:
        return self._get("/movie/now_playing", {"region": region, "page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_release_dates(self, tmdb_id: int) -> list[dict]:
        data = self._get(f"/movie/{tmdb_id}/release_dates")
        return data.get("results", [])

    def get_available_popular(self, page: int = 1, region: str = "US") -> dict:
        """Popular titles TMDB already has a Digital/Physical release date
        for in `region` — Discover excludes theatrical-only titles now that
        Coming Soon is the dedicated place for those. One extra (TTL-cached)
        release_dates call per candidate."""
        popular = self.get_popular(page=page)
        filtered = [
            movie
            for movie in popular.get("results", [])
            if not _lacks_digital_release(self.get_release_dates(movie["id"]), region)
        ]
        return {**popular, "results": filtered}

    def get_available_trending(self, time_window: str = "week", region: str = "US") -> dict:
        """Same digital-availability filter as get_available_popular,
        applied to the Trending row."""
        trending = self.get_trending(time_window=time_window)
        filtered = [
            movie
            for movie in trending.get("results", [])
            if not _lacks_digital_release(self.get_release_dates(movie["id"]), region)
        ]
        return {**trending, "results": filtered}

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_movie_watch_providers(self, tmdb_id: int) -> dict:
        data = self._get(f"/movie/{tmdb_id}/watch/providers")
        return data.get("results", {})

    def search_within_provider(self, query: str, provider_id: int, region: str = "US") -> dict:
        """search_movie results filtered to titles available (any offer
        type — matching discover_by_provider's own behavior) on
        `provider_id` in `region` — the Provider view's own search bar. One
        extra (TTL-cached) watch/providers call per candidate."""
        data = self.search_movie(query)
        filtered = [
            movie
            for movie in data.get("results", [])
            if _available_on_provider(self.get_movie_watch_providers(movie["id"]), region, provider_id)
        ]
        return {**data, "results": filtered}

    # -- TV (Stage 9): mirrors the movie wrappers above, same
    #    key-never-reaches-the-browser rule. --

    def search_tv(self, query: str, year: int | None = None) -> dict:
        params = {"query": query}
        if year:
            params["first_air_date_year"] = year
        return self._get("/search/tv", params)

    def get_tv(self, tmdb_id: int) -> dict:
        return self._get(f"/tv/{tmdb_id}", {"append_to_response": "credits"})

    def get_tv_season(self, tmdb_id: int, season_number: int) -> list[dict]:
        """Episode list (each carrying `episode_number`/`air_date`) for one
        season — the data `tv_resolve.py`'s show-checking logic diffs
        against to notice newly-aired episodes."""
        data = self._get(f"/tv/{tmdb_id}/season/{season_number}")
        return data.get("episodes", [])

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_tv_popular(self, page: int = 1) -> dict:
        return self._get("/tv/popular", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_tv_trending(self, time_window: str = "week") -> dict:
        return self._get(f"/trending/tv/{time_window}")

    def get_coming_soon(self, region: str = "US", page: int = 1) -> dict:
        """Now-playing titles that are both a recent release and have no
        Digital/Physical release date on record for `region` yet — the
        Coming Soon tab's "still in cinemas, no digital release" filter.
        The recency check is free (no request); the digital-release check
        costs one extra (TTL-cached) release_dates call per still-recent
        candidate. Page size/pagination follow TMDB's own now_playing page,
        so a filtered page can come back shorter than a raw one."""
        now_playing = self.get_now_playing(region=region, page=page)
        results = now_playing.get("results", [])
        filtered = [
            movie
            for movie in results
            if _is_recent_release(movie) and _lacks_digital_release(self.get_release_dates(movie["id"]), region)
        ]
        return {**now_playing, "results": filtered}
