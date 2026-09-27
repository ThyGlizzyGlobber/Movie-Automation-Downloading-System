"""The "On Plex" annotation shared by the browse, detail and
recommendation routes."""

from app.db import RequestStore
from app.plex import plex_library_lookup


# ---------------------------------------------------------------------------
# Stage 14: "On Plex" badge — annotates already-fetched TMDB result lists
# (and single-item detail responses) with `on_plex`, using one cached,
# whole-library Plex lookup per request rather than one live Plex call per
# item. See app/plex.py's `plex_library_lookup` for the caching design.
# ---------------------------------------------------------------------------


def _annotate_on_plex(
    results: list[dict], media_type: str, store: RequestStore, *, title_key: str, date_key: str
) -> list[dict]:
    """Returns a *new* list of *new* dicts, `on_plex` added to each — never
    mutates the input items in place. Several of tmdb.py's methods
    (popular/trending/discover-by-provider/coming-soon) are TTL-cached and
    hand back the same item dict object on every cache hit; mutating those
    in place would bake one request's Plex-link state into the shared
    cache for every later hit until the (independent) TMDB cache itself
    expires."""
    matcher = plex_library_lookup(store, media_type)
    annotated = []
    for item in results:
        year_str = (item.get(date_key) or "")[:4]
        year = int(year_str) if year_str.isdigit() else None
        on_plex = bool(matcher(item.get(title_key) or "", year, item.get("id"))) if matcher else False
        annotated.append({**item, "on_plex": on_plex})
    return annotated


def _on_plex_for(title: str, year: int | None, media_type: str, store: RequestStore, tmdb_id: int | None = None) -> bool:
    matcher = plex_library_lookup(store, media_type)
    return bool(matcher(title, year, tmdb_id)) if matcher else False
