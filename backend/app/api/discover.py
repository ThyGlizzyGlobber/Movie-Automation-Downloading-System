"""Browsing TMDB: search, the Discover and TV browse rows, Coming Soon,
and cast pages. Thin pass-throughs of tmdb.py's cached methods, each
result annotated with whether it is already on Plex."""

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import RequestStore
from app.tmdb import BROWSE_SORTS, TMDBClient, TMDBError
from app.api.deps import get_store, get_tmdb, resolve_region, router
from app.api.helpers import _annotate_on_plex


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    year: int | None = None
    provider_id: int | None = None


@router.post("/api/search")
def search(
    body: SearchRequest, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> list[dict]:
    try:
        if body.provider_id is not None:
            data = tmdb.search_within_provider(body.query, body.provider_id)
        else:
            data = tmdb.search_movie(body.query, year=body.year)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    results = data.get("results", [])
    return _annotate_on_plex(results, "movie", store, title_key="title", date_key="release_date")


# -- Stage 4: the browse surface the home grid and provider rows are built
#    from. Thin pass-throughs of tmdb.py's already-TTL-cached methods —
#    same "key never reaches the browser" rule as /api/search. --


@router.get("/api/discover")
def discover_browse(
    genre: int | None = None,
    provider: int | None = None,
    year: int | None = None,
    sort: str = "popular",
    region: str = Depends(resolve_region),
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    """The browse page's one endpoint: every filter at once. Digital-
    availability filtered like the rows above."""
    if sort not in BROWSE_SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {', '.join(BROWSE_SORTS)}")
    try:
        data = tmdb.browse_movies(genre_id=genre, provider_id=provider, year=year, sort=sort, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@router.get("/api/discover/popular")
def discover_popular(page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    # Digital-availability filtered: Coming Soon is the dedicated tab for
    # theatrical-only titles, so Discover shouldn't also surface them.
    try:
        data = tmdb.get_available_popular(page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@router.get("/api/discover/trending")
def discover_trending(
    time_window: str = "week", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_available_trending(time_window=time_window, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@router.get("/api/discover/providers/{provider_id}")
def discover_by_provider(
    provider_id: int,
    region: str = Depends(resolve_region),
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    # Digital-availability filtered — same reasoning as Discover Popular/
    # Trending above: a provider row shouldn't surface a theatrical-only
    # title Coming Soon already owns.
    try:
        data = tmdb.get_available_by_provider(provider_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@router.get("/api/discover/genre/{genre_id}")
def discover_by_genre(
    genre_id: int,
    region: str = Depends(resolve_region),
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    # Digital-availability filtered — same reasoning as Discover Popular/
    # Trending above.
    try:
        data = tmdb.get_available_by_genre(genre_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


@router.get("/api/discover/coming-soon")
def discover_coming_soon(
    region: str = Depends(resolve_region), page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_coming_soon(region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "movie", store, title_key="title", date_key="release_date")
    return data


# -- Stage 14: TV browse surface — the show equivalent of the movie routes
#    above. Same thin-pass-through/on_plex-annotation/key-never-reaches-
#    the-browser rules. --


@router.get("/api/tv/discover")
def tv_discover_browse(
    genre: int | None = None,
    provider: int | None = None,
    year: int | None = None,
    sort: str = "popular",
    region: str = Depends(resolve_region),
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    # Registered ahead of /api/tv/{tmdb_id} so the literal segment wins.
    if sort not in BROWSE_SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {', '.join(BROWSE_SORTS)}")
    try:
        data = tmdb.browse_tv(genre_id=genre, provider_id=provider, year=year, sort=sort, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/tv/discover/popular")
def tv_discover_popular(
    page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    # Filtered to shows that have aired at least one episode — Coming Soon
    # is the dedicated place for anything that hasn't, same rule as the
    # movie side's digital-availability filter above.
    try:
        data = tmdb.get_available_tv_popular(page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/tv/discover/trending")
def tv_discover_trending(
    time_window: str = "week", page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_available_tv_trending(time_window=time_window, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/tv/discover/providers/{provider_id}")
def tv_discover_by_provider(
    provider_id: int,
    region: str = Depends(resolve_region),
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    try:
        data = tmdb.get_available_tv_by_provider(provider_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/tv/discover/genre/{genre_id}")
def tv_discover_by_genre(
    genre_id: int,
    region: str = Depends(resolve_region),
    page: int = 1,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    try:
        data = tmdb.get_available_tv_by_genre(genre_id, region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/tv/discover/coming-soon")
def tv_discover_coming_soon(
    region: str = Depends(resolve_region), page: int = 1, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> dict:
    try:
        data = tmdb.get_tv_coming_soon(region=region, page=page)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    data["results"] = _annotate_on_plex(data.get("results", []), "show", store, title_key="name", date_key="first_air_date")
    return data


@router.get("/api/person/{person_id}")
def get_person_detail(person_id: int, tmdb: TMDBClient = Depends(get_tmdb)) -> dict:
    """A cast member's filmography — backs the "click an actor" detail
    page. Only what the frontend actually needs: name/photo plus a
    deduped, newest-first credits list (each item already carrying its
    own media_type, so the frontend's existing posterCard() works on it
    unchanged, same as any other mixed movie/TV list in this app).

    Deduped on (id, media_type) — TMDB's combined_credits can list the
    same title more than once for a recurring/guest role across an
    actor's episodic appearances, which would otherwise show as a
    duplicate poster."""
    try:
        person = tmdb.get_person(person_id)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"person_id {person_id} not found") from exc
    cast = person.get("combined_credits", {}).get("cast", [])
    seen: set[tuple] = set()
    credits = []
    for c in sorted(cast, key=lambda c: c.get("release_date") or c.get("first_air_date") or "", reverse=True):
        key = (c.get("id"), c.get("media_type"))
        if key in seen:
            continue
        seen.add(key)
        credits.append(c)
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "profile_path": person.get("profile_path"),
        "known_for_department": person.get("known_for_department"),
        "credits": credits,
    }


@router.post("/api/tv/search")
def search_tv(
    body: SearchRequest, store: RequestStore = Depends(get_store), tmdb: TMDBClient = Depends(get_tmdb)
) -> list[dict]:
    try:
        if body.provider_id is not None:
            data = tmdb.search_tv_within_provider(body.query, body.provider_id)
        else:
            data = tmdb.search_tv(body.query, year=body.year)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    results = data.get("results", [])
    return _annotate_on_plex(results, "show", store, title_key="name", date_key="first_air_date")
