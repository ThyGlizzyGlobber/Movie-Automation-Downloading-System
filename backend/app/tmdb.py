"""TMDB client wrapper. Called only from the backend — the key never
reaches the browser; the frontend hotlinks TMDB's public image CDN
directly instead of proxying images."""

import requests

from app.cache import ttl_cache

BASE_URL = "https://api.themoviedb.org/3"
POPULAR_DISCOVER_TTL_SECONDS = 300


class TMDBError(RuntimeError):
    pass


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
        return self._get(f"/movie/{tmdb_id}")

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
