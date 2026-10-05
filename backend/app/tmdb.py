"""TMDB client wrapper. Called only from the backend — the key never
reaches the browser; the frontend hotlinks TMDB's public image CDN
directly instead of proxying images."""

import concurrent.futures
from datetime import datetime, timedelta, timezone

import requests

from app.cache import ttl_cache

BASE_URL = "https://api.themoviedb.org/3"
POPULAR_DISCOVER_TTL_SECONDS = 60 * 60
# Past the TTL a list is still served, at once, while it refreshes in the
# background (see app.cache.ttl_cache). TMDB's lists move daily at most;
# what cost time was every five-minute expiry making whoever came next
# wait 1-3s while a landing page's worth of them was fetched again.
LIST_STALE_SECONDS = 24 * 60 * 60
COLLECTION_TTL_SECONDS = 6 * 60 * 60
# One title's detail, season or videos. Every content page view went to
# TMDB live (250-650ms measured), and the hero rebuilt five of them at a
# time. Half an hour is also the most the worker's show checks can lag
# behind TMDB for a new season or episode, against a check interval
# measured in hours. Capped lower than the lists: a detail with credits,
# images and recommendations appended runs to a couple of hundred KB.
DETAIL_TTL_SECONDS = 30 * 60
DETAIL_CACHE_ENTRIES = 300

# Release-date lookups run at once per page of results (see
# _digitally_released). Bounded because a landing page asks for many pages
# at the same moment, and TMDB is a shared, rate-limited service.
RELEASE_DATE_WORKERS = 8
# Connections kept open to TMDB — enough for a landing page's lookups to
# run at once rather than queue for a free one (see _pooled_session).
HTTP_POOL_SIZE = 32

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


def _lacks_digital_release(release_dates_by_country: list[dict]) -> bool:
    """True when no country on record has a Digital or Physical release
    dated today or earlier — i.e. nothing anywhere says this has left
    cinemas yet.

    Asks every region rather than the household's, and that is the whole
    point of it. A digital release is a worldwide event as far as this
    app is concerned: once a copy exists it is on the indexers, whatever
    a territory's own paperwork says. The same reasoning already runs in
    releaseWindow.ts's cascade and in api/hero.py's _arriving_soon.

    It used to ask one region and treat that region having no entry as
    evidence of no release, which is only safe where TMDB's coverage is
    good. Obsession, checked 2026-09-27: digital in the US and GB since
    2026-06-30 and in five more territories since 2026-07-17, while its
    Australian record holds nothing but a premiere and a cinema date. An
    Australian household was told a film it already had on Plex was not
    out yet, and the Add to Plex button was disabled on that basis.

    The asymmetry decides it. Wrongly saying "still in cinemas" locks
    someone out of a film that exists; wrongly saying "out" costs a
    search that finds nothing, which the pipeline already handles."""
    now = datetime.now(timezone.utc)
    for country in release_dates_by_country:
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


def is_movie_coming_soon(movie: dict, release_dates_by_country: list[dict]) -> bool:
    """The single combined "is this movie Coming Soon" predicate — used
    both to build the Coming Soon list itself and to flag one movie's own
    detail page (disables its Add to Plex button there). Recent *and*
    lacking a digital release, same two-part check as get_coming_soon: the
    recency half is what keeps an old catalog title with no Digital/
    Physical record on file (TMDB just never logged one) from reading as
    "coming soon" forever."""
    return _is_recent_release(movie) and _lacks_digital_release(release_dates_by_country)


def trailer_candidates(videos: list[dict]) -> list[dict]:
    """Every YouTube video worth considering as a hero preview, best
    type-guess first — official Trailer, any Trailer, official Teaser,
    any Teaser.

    A list, because type is a poor guide to length and length is what
    the hero actually wants (trailers.pick_shortest_suitable measures
    them). Measured live, 2026-09-21: Inside Out 2 offers four official
    Teasers of 15-30s — "Best Movie of the Year", "#1 Movie is Certified
    Fresh" — which are social-media stings, not short trailers, against
    a 98s Announce Trailer that is the only real preview on file.
    Mission: Impossible's teasers run 5s. Preferring Teasers outright
    would have picked those. The Scandal, meanwhile, has a 61s official
    Teaser against a 92s Trailer, and there the Teaser is exactly the
    shorter cut worth having. Only measuring tells those apart.

    Still ranked, because the ranking is the tiebreak among equals and
    the fallback when nothing can be measured."""
    youtube = [v for v in videos if v.get("site") == "YouTube" and v.get("key")]
    ranked: list[dict] = []
    for video_type in ("Trailer", "Teaser"):
        for official_only in (True, False):
            for v in youtube:
                if v.get("type") != video_type:
                    continue
                if official_only and not v.get("official"):
                    continue
                if v not in ranked:
                    ranked.append(v)
    return ranked


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


def _aired_only(data: dict) -> dict:
    """A TMDB results page with every not-yet-aired show dropped."""
    return {**data, "results": [show for show in data.get("results", []) if not is_tv_upcoming(show)]}


# Browse-page sort keys as the frontend sends them, mapped to TMDB's own
# sort_by values. "trending" is not here: it is served by the separate
# trending endpoints and takes no other filters.
BROWSE_SORTS = ("popular", "newest", "rated")
BROWSE_SORTS_MOVIE = {"popular": "popularity.desc", "newest": "primary_release_date.desc", "rated": "vote_average.desc"}
BROWSE_SORTS_TV = {"popular": "popularity.desc", "newest": "first_air_date.desc", "rated": "vote_average.desc"}


def best_logo_path(images: dict | None) -> str | None:
    """The one title logo worth showing: English over language-less,
    PNG over SVG (the browser gets a plain <img>), then the best-rated,
    as TMDB's own "rating" sort orders them. None when TMDB has no logo
    for the title."""
    logos = (images or {}).get("logos") or []
    if not logos:
        return None

    def rank(logo: dict) -> tuple:
        lang = logo.get("iso_639_1")
        path = logo.get("file_path") or ""
        return (
            0 if lang == "en" else 1 if lang in (None, "") else 2,
            0 if path.lower().endswith(".png") else 1,
            # Rating before vote count: a vote can be a thumbs-down. Pacific
            # Rim (68726) carries Ready Player One's logo, voted down to 0.5
            # on five votes, and counting votes first put it over the real
            # one at 3.3 on one.
            -(logo.get("vote_average") or 0),
            -(logo.get("vote_count") or 0),
        )

    best = sorted(logos, key=rank)[0]
    return best.get("file_path") or None


def logo_aspect(images: dict | None, path: str | None) -> float | None:
    """Width over height of the logo at `path`, from TMDB's own metadata.
    The page needs it before the image arrives to say how wide the logo
    will be drawn — a squarish mark is held by the banner's height to a
    fraction of a wide one's width — and so fetch the smaller encoding
    when that is all the screen needs (app/logos.py)."""
    for logo in (images or {}).get("logos") or []:
        if path and logo.get("file_path") == path:
            ratio = logo.get("aspect_ratio")
            if not ratio and logo.get("width") and logo.get("height"):
                ratio = logo["width"] / logo["height"]
            return round(float(ratio), 3) if ratio else None
    return None


def _pooled_session() -> requests.Session:
    """A session that can hold HTTP_POOL_SIZE connections to TMDB at once.
    requests' default keeps ten; a landing page asks for a few hundred
    lookups together (see _digitally_released and api/recommendations.py),
    and past ten each one was waiting for a connection or opening a fresh
    TLS one — measured, most of a cold Home's nine seconds."""
    session = requests.Session()
    session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=HTTP_POOL_SIZE))
    return session


class TMDBClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        # Deliberately not raised here (frontend migration Part E): the
        # backend needs to boot successfully — and serve the first-run
        # setup wizard's own routes — even before a TMDB key exists
        # anywhere (env or the settings-table fallback), which is exactly
        # the state a fresh, not-yet-configured install starts in. The
        # same "not configured" failure just happens on first real call
        # instead of at construction time.
        self.api_key = api_key
        self.session = session or _pooled_session()

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.api_key:
            raise TMDBError("TMDB API key is not configured")
        params = dict(params or {})
        params["api_key"] = self.api_key
        response = self.session.get(f"{BASE_URL}{path}", params=params, timeout=10)
        if not response.ok:
            raise TMDBError(f"TMDB {path} failed: {response.status_code} {response.text[:200]}")
        return response.json()

    # -- per-query lookups. Searches are not cached; a title's own detail
    #    is, briefly (DETAIL_TTL_SECONDS) — every page view of it asks. --

    def search_movie(self, query: str, year: int | None = None) -> dict:
        params = {"query": query}
        if year:
            params["year"] = year
        return self._get("/search/movie", params)

    @ttl_cache(DETAIL_TTL_SECONDS, max_entries=DETAIL_CACHE_ENTRIES)
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
            f"/movie/{tmdb_id}",
            {
                "append_to_response": "credits,release_dates,recommendations,watch/providers,images",
                # Title logos (transparent art) for the hero and detail
                # page — English first, then language-less marks.
                "include_image_language": "en,null",
            },
        )

    @ttl_cache(COLLECTION_TTL_SECONDS)
    def get_collection(self, collection_id: int) -> dict:
        """A franchise as TMDB groups it — "National Treasure Collection"
        and every film in it. Cached for longer than the browse rows: a
        collection gains a part once in years, and every film in it asks
        for the same one."""
        return self._get(f"/collection/{collection_id}")

    @ttl_cache(DETAIL_TTL_SECONDS, max_entries=DETAIL_CACHE_ENTRIES)
    def get_movie_videos(self, tmdb_id: int) -> list[dict]:
        return self._get(f"/movie/{tmdb_id}/videos").get("results", [])

    @ttl_cache(DETAIL_TTL_SECONDS, max_entries=DETAIL_CACHE_ENTRIES)
    def get_tv_videos(self, tmdb_id: int) -> list[dict]:
        return self._get(f"/tv/{tmdb_id}/videos").get("results", [])

    # -- browse surface: TTL-cached, since these are repeatedly hit by the
    #    home grid and provider rows rather than being per-title lookups --

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_popular(self, page: int = 1) -> dict:
        return self._get("/movie/popular", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_trending(self, time_window: str = "week", page: int = 1) -> dict:
        return self._get(f"/trending/movie/{time_window}", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
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

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def discover_curated(self, media_type: str, **params) -> dict:
        """A discover call with the filters passed straight through.

        The existing discover_movies/discover_tv take a fixed handful of
        filters because the browse UI offers a fixed handful. The named
        rows ("Pitch-Black Comedies", "Sci-Fi That Earns Its Ending") are
        defined by combinations those can't express — two genres at once, a
        decade, a rating floor with a vote floor under it so the floor means
        something. Rather than grow that signature a filter at a time, this
        one hands TMDB whatever the mood asked for.

        Safe to cache on kwargs because every value a mood carries is a
        string or an int — see moods.py, where the queries live."""
        path = "/discover/tv" if media_type == "tv" else "/discover/movie"
        return self._get(path, {"include_adult": "false", **params})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_movie_recommendations(self, tmdb_id: int, page: int = 1) -> dict:
        """TMDB's own "if you liked this" for a movie — the seed of every
        "Because you watched…" row.

        `/recommendations` rather than `/similar`: similar is computed from
        shared genres and keywords, which reliably returns the same handful
        of blockbusters for anything in a broad genre. Recommendations are
        derived from what TMDB's users actually co-watch, which is the
        question being asked here.

        Cached on the same TTL as the discover rows: a recommendation set
        for a given title barely moves day to day, and a household browsing
        the same Home page shouldn't each pay for the lookup."""
        return self._get(f"/movie/{tmdb_id}/recommendations", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_tv_recommendations(self, tmdb_id: int, page: int = 1) -> dict:
        """The TV half of `get_movie_recommendations` — same reasoning, and
        kept separate because TMDB keys movies and shows in different
        namespaces, so an id alone can't tell you which endpoint it wants."""
        return self._get(f"/tv/{tmdb_id}/recommendations", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def discover_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        return self._get(
            "/discover/movie",
            {"with_genres": genre_id, "region": region, "page": page, "sort_by": "popularity.desc"},
        )

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_now_playing(self, region: str = "US", page: int = 1) -> dict:
        return self._get("/movie/now_playing", {"region": region, "page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_release_dates(self, tmdb_id: int) -> list[dict]:
        data = self._get(f"/movie/{tmdb_id}/release_dates")
        return data.get("results", [])

    def _digitally_released(self, data: dict) -> dict:
        """A TMDB results page with every theatrical-only title dropped —
        one extra (TTL-cached) release_dates call per candidate.

        Asked together rather than in turn: twenty lookups one after the
        other were 4-5s for a page on a cold cache, every movie row and
        browse page paying it. A failed lookup still fails the page, as it
        did when they ran in sequence."""
        results = data.get("results", [])
        if not results:
            return {**data, "results": []}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(RELEASE_DATE_WORKERS, len(results))) as pool:
            lacking = list(pool.map(lambda movie: _lacks_digital_release(self.get_release_dates(movie["id"])), results))
        return {**data, "results": [movie for movie, lacks in zip(results, lacking) if not lacks]}

    def get_available_popular(self, page: int = 1, region: str = "US") -> dict:
        """Popular titles TMDB already has a Digital/Physical release date
        for anywhere — Discover excludes theatrical-only titles now that
        Coming Soon is the dedicated place for those. One extra (TTL-cached)
        release_dates call per candidate. `region` is accepted but unused:
        _lacks_digital_release deliberately asks every country."""
        return self._digitally_released(self.get_popular(page=page))

    def get_available_trending(self, time_window: str = "week", region: str = "US", page: int = 1) -> dict:
        """Same digital-availability filter as get_available_popular,
        applied to the Trending row. `region` is unused, as there."""
        return self._digitally_released(self.get_trending(time_window=time_window, page=page))

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
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

    @ttl_cache(DETAIL_TTL_SECONDS, max_entries=DETAIL_CACHE_ENTRIES)
    def get_tv(self, tmdb_id: int) -> dict:
        # content_ratings folds in the show's own per-country age ratings
        # (TV's equivalent of a movie's release_dates.certification) —
        # the home hero carousel's AU rating badge reads this straight off
        # this same call, no separate request. recommendations backs the
        # detail page's "Related" row.
        return self._get(
            f"/tv/{tmdb_id}",
            # external_ids: the TheTVDB/IMDb ids tvmaze.py needs to reach
            # a show's episode release *times*, which TMDB has no field
            # for. Folded into this existing call rather than a second
            # request, same as everything else appended here.
            {
                "append_to_response": "credits,content_ratings,recommendations,images,external_ids",
                "include_image_language": "en,null",
            },
        )

    def get_person(self, person_id: int) -> dict:
        # append_to_response folds their whole filmography (movies + TV)
        # into the one call — same "one call, not N" pattern get_movie/
        # get_tv already use for their own credits.
        return self._get(f"/person/{person_id}", {"append_to_response": "combined_credits"})

    @ttl_cache(DETAIL_TTL_SECONDS, max_entries=DETAIL_CACHE_ENTRIES)
    def get_tv_season(self, tmdb_id: int, season_number: int) -> list[dict]:
        """Episode list (each carrying `episode_number`/`air_date`) for one
        season — the data `tv_resolve.py`'s show-checking logic diffs
        against to notice newly-aired episodes."""
        data = self._get(f"/tv/{tmdb_id}/season/{season_number}")
        return data.get("episodes", [])

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_tv_popular(self, page: int = 1) -> dict:
        return self._get("/tv/popular", {"page": page})

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_tv_trending(self, time_window: str = "week", page: int = 1) -> dict:
        return self._get(f"/trending/tv/{time_window}", {"page": page})

    # -- Stage 14.x: provider (streaming service) browse/search for TV,
    #    mirroring discover_by_provider/get_movie_watch_providers/
    #    search_within_provider above. TMDB's watch-provider ids are shared
    #    across movie/TV (Netflix is always 8, etc.), so the frontend's
    #    existing curated provider list needs no changes, just a TV-shaped
    #    call for each. --

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
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

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def discover_tv_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        return self._get(
            "/discover/tv",
            {"with_genres": genre_id, "region": region, "page": page, "sort_by": "popularity.desc"},
        )

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
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
        return _aired_only(self.get_tv_popular(page=page))

    def get_available_tv_trending(self, time_window: str = "week", page: int = 1) -> dict:
        return _aired_only(self.get_tv_trending(time_window=time_window, page=page))

    def get_available_tv_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        return _aired_only(self.discover_tv_by_genre(genre_id, region=region, page=page))

    def get_available_tv_by_provider(self, provider_id: int, region: str = "US", page: int = 1) -> dict:
        return _aired_only(self.discover_tv_by_provider(provider_id, region=region, page=page))

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def _discover_tv_upcoming(self, region: str = "US", page: int = 1) -> dict:
        # From tomorrow: a show premiering today already counts as aired
        # (is_tv_upcoming), and sorted soonest first, today's premieres
        # alone can fill page 1, leaving the Coming soon row empty (live
        # 2026-09-18: 0 of 20 kept).
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        return self._get(
            "/discover/tv",
            {
                "first_air_date.gte": tomorrow,
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
            if _is_recent_release(movie) and is_movie_coming_soon(movie, self.get_release_dates(movie["id"]))
        ]
        return {**now_playing, "results": filtered}

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def get_digital_calendar(self, region: str = "US", days: int = 60, page: int = 1) -> dict:
        """Films whose digital or disc release lands in the next `days`,
        most popular first — "what is about to become gettable".

        Not get_coming_soon, which reads /movie/now_playing and asks the
        opposite question: what is in cinemas and has no digital date on
        record. Those overlap but the difference is the whole feature.
        Checked 2026-09-26 with Spider-Man: Brand New Day, the most
        popular title on TMDB at 630 and three days from digital — it is
        absent from every page of now_playing (it opened two months
        earlier and has long since dropped off), so get_coming_soon
        cannot see it at all, while this query returns it first.

        Ordered by popularity rather than by date on purpose: a hero has
        five slots and the question it answers is "what is arriving that
        you would care about", not "what is arriving soonest", which
        reliably surfaces the smallest film of the week.
        """
        today = datetime.now(timezone.utc).date()
        return self._get(
            "/discover/movie",
            {
                "region": region,
                "with_release_type": f"{_DIGITAL_RELEASE_TYPE}|{_PHYSICAL_RELEASE_TYPE}",
                "release_date.gte": today.isoformat(),
                "release_date.lte": (today + timedelta(days=days)).isoformat(),
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "page": page,
            },
        )

    def get_available_by_genre(self, genre_id: int, region: str = "US", page: int = 1) -> dict:
        """Same digital-availability filter as get_available_popular/
        get_available_trending, applied to a genre row — without this, a
        genre row can surface a theatrical-only title Coming Soon is
        already responsible for."""
        return self._digitally_released(self.discover_by_genre(genre_id, region=region, page=page))

    def get_available_by_provider(self, provider_id: int, region: str = "US", page: int = 1) -> dict:
        """Same digital-availability filter, applied to a provider row."""
        return self._digitally_released(self.discover_by_provider(provider_id, region=region, page=page))

    # -- Browse page: one Discover call that takes every filter the browse
    #    page offers at once (genre, streaming service, year, sort), so a
    #    genre row's "See all" and the filter chips on the page it opens
    #    share one code path. Availability-filtered like the rows. --

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def discover_movies(
        self,
        *,
        genre_id: int | None = None,
        provider_id: int | None = None,
        year: int | None = None,
        sort: str = "popular",
        region: str = "US",
        page: int = 1,
    ) -> dict:
        params: dict = {
            "page": page,
            "region": region,
            "sort_by": BROWSE_SORTS_MOVIE.get(sort, BROWSE_SORTS_MOVIE["popular"]),
            "include_adult": "false",
        }
        if genre_id:
            params["with_genres"] = genre_id
        if provider_id:
            params["with_watch_providers"] = provider_id
            params["watch_region"] = region
        if year:
            params["primary_release_year"] = year
        if sort == "newest":
            # Newest means "newest that is actually out", not TMDB's
            # far-future placeholder entries; the small vote floor keeps
            # unreleased-in-practice titles with no audience off the top.
            params["primary_release_date.lte"] = datetime.now(timezone.utc).date().isoformat()
            params["vote_count.gte"] = 20
        elif sort == "rated":
            params["vote_count.gte"] = 200
        return self._get("/discover/movie", params)

    def browse_movies(self, *, region: str = "US", **filters) -> dict:
        return self._digitally_released(self.discover_movies(region=region, **filters))

    @ttl_cache(POPULAR_DISCOVER_TTL_SECONDS, stale_seconds=LIST_STALE_SECONDS)
    def discover_tv(
        self,
        *,
        genre_id: int | None = None,
        provider_id: int | None = None,
        year: int | None = None,
        sort: str = "popular",
        region: str = "US",
        page: int = 1,
    ) -> dict:
        params: dict = {
            "page": page,
            "sort_by": BROWSE_SORTS_TV.get(sort, BROWSE_SORTS_TV["popular"]),
            "include_adult": "false",
        }
        if genre_id:
            params["with_genres"] = genre_id
        if provider_id:
            params["with_watch_providers"] = provider_id
            params["watch_region"] = region
        if year:
            params["first_air_date_year"] = year
        if sort == "newest":
            params["first_air_date.lte"] = datetime.now(timezone.utc).date().isoformat()
            params["vote_count.gte"] = 20
        elif sort == "rated":
            params["vote_count.gte"] = 200
        return self._get("/discover/tv", params)

    def browse_tv(self, *, region: str = "US", **filters) -> dict:
        return _aired_only(self.discover_tv(region=region, **filters))
