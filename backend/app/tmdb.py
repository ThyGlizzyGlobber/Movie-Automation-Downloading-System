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


def is_movie_coming_soon(movie: dict, release_dates_by_country: list[dict], region: str) -> bool:
    """The single combined "is this movie Coming Soon" predicate — used
    both to build the Coming Soon list itself and to flag one movie's own
    detail page (disables its Add to Plex button there). Recent *and*
    lacking a digital release, same two-part check as get_coming_soon: the
    recency half is what keeps an old catalog title with no Digital/
    Physical record on file (TMDB just never logged one) from reading as
    "coming soon" forever."""
    return _is_recent_release(movie) and _lacks_digital_release(release_dates_by_country, region)


def best_trailer_key(videos: list[dict]) -> str | None:
    """The single best YouTube trailer key from a /videos response's
    `results` list, for the home hero carousel's background video — or
    None if nothing suitable exists (a title with no trailer on file at
    all, or only non-YouTube/non-trailer entries). Preference order:
    an official Trailer, any Trailer, an official Teaser, any Teaser —
    a Teaser is a real, if lesser, substitute when no full trailer has
    been uploaded yet (common for a just-announced or still-airing
    season), but never anything further afield (a clip, a featurette,
    a bloopers reel) that wouldn't read as "the trailer" to a viewer."""
    youtube = [v for v in videos if v.get("site") == "YouTube" and v.get("key")]

    def pick(video_type: str, official_only: bool) -> dict | None:
        candidates = [v for v in youtube if v.get("type") == video_type and (not official_only or v.get("official"))]
        return candidates[0] if candidates else None

    for video_type in ("Trailer", "Teaser"):
        for official_only in (True, False):
            match = pick(video_type, official_only)
            if match:
                return match["key"]
    return None


def is_tv_upcoming(show: dict) -> bool:
    """The TV equivalent of a movie lacking a digital release: no episode
    has aired yet. Unlike movies, this needs no extra per-title request —
    `first_air_date` is already present on every TV discover/popular/
    trending/genre/provider result, so this is a free, no-request check."""
    first_air_date = show.get("first_air_date")
    if not first_air_date:
        return True
    aired_at = datetime.fromisoformat(first_air_date).replace(tzinfo=timezone.utc)
    return aired_at > datetime.now(timezone.utc)


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
        # append_to_response folds cast/crew, per-region certifications,
        # recommendations, and watch providers into the one call the
        # detail view needs (Stage 4 addendum: cast, crew, and a
        # certification badge on the movie detail page; later addendum:
        # a "Related" row; later addendum: a movie has no `networks`
        # field the way a TV show does, so the frontend's originals
        # badge falls back to this for titles where the actual credited
        # production company isn't the streamer — e.g. a Lionsgate-made
        # film Netflix only holds exclusive distribution rights to).
        return self._get(
            f"/movie/{tmdb_id}", {"append_to_response": "credits,release_dates,recommendations,watch/providers"}
        )

    def get_alternative_titles(self, tmdb_id: int) -> list[dict]:
        data = self._get(f"/movie/{tmdb_id}/alternative_titles")
        return data.get("titles", [])

    def get_movie_videos(self, tmdb_id: int) -> list[dict]:
        return self._get(f"/movie/{tmdb_id}/videos").get("results", [])

    def get_tv_videos(self, tmdb_id: int) -> list[dict]:
        return self._get(f"/tv/{tmdb_id}/videos").get("results", [])

    # -- browse surface: TTL-cached, since these are repeatedly hit by the
    #    home grid and provider rows rather than being per-title lookups --

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_popular(self, page: int = 1) -> dict:
        return self._get("/movie/popular", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_trending(self, time_window: str = "week", page: int = 1) -> dict:
        return self._get(f"/trending/movie/{time_window}", {"page": page})

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
    def discover_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        return self._get(
            "/discover/movie",
            {"with_genres": genre_id, "region": region, "page": page, "sort_by": "popularity.desc"},
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

    def get_available_trending(self, time_window: str = "week", region: str = "US", page: int = 1) -> dict:
        """Same digital-availability filter as get_available_popular,
        applied to the Trending row."""
        trending = self.get_trending(time_window=time_window, page=page)
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
        # content_ratings folds in the show's own per-country age ratings
        # (TV's equivalent of a movie's release_dates.certification) —
        # the home hero carousel's AU rating badge reads this straight off
        # this same call, no separate request. recommendations backs the
        # detail page's "Related" row.
        return self._get(f"/tv/{tmdb_id}", {"append_to_response": "credits,content_ratings,recommendations"})

    def get_person(self, person_id: int) -> dict:
        # append_to_response folds their whole filmography (movies + TV)
        # into the one call — same "one call, not N" pattern get_movie/
        # get_tv already use for their own credits.
        return self._get(f"/person/{person_id}", {"append_to_response": "combined_credits"})

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
    def get_tv_trending(self, time_window: str = "week", page: int = 1) -> dict:
        return self._get(f"/trending/tv/{time_window}", {"page": page})

    # -- Stage 14.x: provider (streaming service) browse/search for TV,
    #    mirroring discover_by_provider/get_movie_watch_providers/
    #    search_within_provider above. TMDB's watch-provider ids are shared
    #    across movie/TV (Netflix is always 8, etc.), so the frontend's
    #    existing curated provider list needs no changes, just a TV-shaped
    #    call for each. --

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def discover_tv_by_provider(self, provider_id: int, region: str = "US", page: int = 1) -> dict:
        return self._get(
            "/discover/tv",
            {
                "with_watch_providers": provider_id,
                "watch_region": region,
                "page": page,
                "sort_by": "popularity.desc",
            },
        )

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def discover_tv_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        return self._get(
            "/discover/tv",
            {"with_genres": genre_id, "region": region, "page": page, "sort_by": "popularity.desc"},
        )

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def get_tv_watch_providers(self, tmdb_id: int) -> dict:
        data = self._get(f"/tv/{tmdb_id}/watch/providers")
        return data.get("results", {})

    # -- TV Coming Soon: the show equivalent of a movie's digital-release
    #    gate. TV has no digital/physical release concept, so "not yet
    #    available" here means "hasn't aired a first episode yet" — a
    #    condition already visible on every plain discover/popular/
    #    trending/genre/provider result via first_air_date, at zero extra
    #    request cost (unlike the movie side's per-title release_dates
    #    call). --

    def get_available_tv_popular(self, page: int = 1) -> dict:
        popular = self.get_tv_popular(page=page)
        filtered = [show for show in popular.get("results", []) if not is_tv_upcoming(show)]
        return {**popular, "results": filtered}

    def get_available_tv_trending(self, time_window: str = "week", page: int = 1) -> dict:
        trending = self.get_tv_trending(time_window=time_window, page=page)
        filtered = [show for show in trending.get("results", []) if not is_tv_upcoming(show)]
        return {**trending, "results": filtered}

    def get_available_tv_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        discover = self.discover_tv_by_genre(genre_id, region=region, page=page)
        filtered = [show for show in discover.get("results", []) if not is_tv_upcoming(show)]
        return {**discover, "results": filtered}

    def get_available_tv_by_provider(self, provider_id: int, region: str = "US", page: int = 1) -> dict:
        discover = self.discover_tv_by_provider(provider_id, region=region, page=page)
        filtered = [show for show in discover.get("results", []) if not is_tv_upcoming(show)]
        return {**discover, "results": filtered}

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS)
    def _discover_tv_upcoming(self, region: str = "US", page: int = 1) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        return self._get(
            "/discover/tv",
            {
                "first_air_date.gte": today,
                "watch_region": region,
                "page": page,
                "sort_by": "first_air_date.asc",
            },
        )

    def get_tv_coming_soon(self, region: str = "US", page: int = 1) -> dict:
        """Shows with a first-air-date today or later — the TV Coming Soon
        tab. is_tv_upcoming is applied on top of TMDB's own date filter
        (rather than trusted alone) so a show sitting right on today's
        boundary is judged by the same rule used everywhere else."""
        discover = self._discover_tv_upcoming(region=region, page=page)
        filtered = [show for show in discover.get("results", []) if is_tv_upcoming(show)]
        return {**discover, "results": filtered}

    def search_tv_within_provider(self, query: str, provider_id: int, region: str = "US") -> dict:
        data = self.search_tv(query)
        filtered = [
            show
            for show in data.get("results", [])
            if _available_on_provider(self.get_tv_watch_providers(show["id"]), region, provider_id)
        ]
        return {**data, "results": filtered}

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
        # `and`'s short-circuit is load-bearing here, not just style: it's
        # what keeps get_release_dates() (a real request on a cache miss)
        # from firing for every not-recent movie, not only the recent ones
        # that actually need the check.
        filtered = [
            movie
            for movie in results
            if _is_recent_release(movie) and is_movie_coming_soon(movie, self.get_release_dates(movie["id"]), region)
        ]
        return {**now_playing, "results": filtered}

    def get_available_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        """Same digital-availability filter as get_available_popular/
        get_available_trending, applied to a genre row — without this, a
        genre row can surface a theatrical-only title Coming Soon is
        already responsible for."""
        discover = self.discover_by_genre(genre_id, region=region, page=page)
        filtered = [
            movie
            for movie in discover.get("results", [])
            if not _lacks_digital_release(self.get_release_dates(movie["id"]), region)
        ]
        return {**discover, "results": filtered}

    def get_available_by_provider(self, provider_id: int, region: str = "US", page: int = 1) -> dict:
        """Same digital-availability filter, applied to a provider row."""
        discover = self.discover_by_provider(provider_id, region=region, page=page)
        filtered = [
            movie
            for movie in discover.get("results", [])
            if not _lacks_digital_release(self.get_release_dates(movie["id"]), region)
        ]
        return {**discover, "results": filtered}
