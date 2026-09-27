"""Plex: linking the admin's account and picking a server, plus the Home
rows and artwork read from the linked server."""

import concurrent.futures
import secrets
import time

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, Field

from app.db import RequestStore, SessionRow
from app.plex import PlexClient, PlexError, PlexLinker, client_from_settings, locate_title
from app.api.deps import (
    _client_ip,
    _new_session_expiry,
    _set_session_cookie,
    get_plex_linker,
    get_store,
    logger,
    require_admin,
    require_admin_or_setup_bootstrap,
    require_session,
    router,
)
from app.api.main import app


# -- Plex account linking (admin server-linking, PIN sign-in). The
#    resulting token is stored server-side only — these routes never
#    return it. See app/plex.py. Gated by require_admin_or_setup_bootstrap
#    (frontend migration Part C3): unauthenticated-but-token-checked while
#    no server is linked yet (there's no admin to authenticate as before
#    this completes — the admin's identity *is* whoever completes it),
#    admin-only afterward (re-linking/switching is an ongoing admin
#    action, not a bootstrap one). --


@app.post("/api/plex/link", dependencies=[Depends(require_admin_or_setup_bootstrap)])
async def start_plex_link(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    try:
        auth_url = await linker.start()
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"auth_url": auth_url}


@app.get("/api/plex/status", dependencies=[Depends(require_admin_or_setup_bootstrap)])
def plex_status(linker: PlexLinker = Depends(get_plex_linker)) -> dict:
    return linker.status()


@app.post("/api/plex/unlink", dependencies=[Depends(require_admin)])
def unlink_plex(
    request: Request, linker: PlexLinker = Depends(get_plex_linker), store: RequestStore = Depends(get_store)
) -> dict:
    linker.unlink()
    store.record_auth_event("plex_unlinked", ip_address=_client_ip(request))
    return linker.status()


def _linked_account_resources(store: RequestStore) -> tuple[PlexClient, str, list[dict]]:
    """The signed-in Plex account's client, token and plex.tv resources —
    the first step of both listing its servers and picking one."""
    settings = store.get_settings()
    token = settings.get("plex_token")
    client_id = settings.get("plex_client_id")
    if not token or not client_id:
        raise HTTPException(status_code=409, detail="no Plex account linked yet — sign in first")
    client = PlexClient(client_id)
    try:
        resources = client.list_resources(token)
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return client, token, resources


@app.get("/api/plex/servers", dependencies=[Depends(require_admin_or_setup_bootstrap)])
def list_plex_servers(store: RequestStore = Depends(get_store)) -> list[dict]:
    """Every Plex server this account *owns*, using the already-persisted
    account-level token — no fresh PIN sign-in needed (frontend migration
    Part C1). Backs both the setup wizard's server picker (an account can
    own more than one server) and Settings' "Switch Server" action."""
    client, token, resources = _linked_account_resources(store)
    return [
        {"name": r["name"], "machine_identifier": r["machine_identifier"]} for r in resources if r["owned"]
    ]


class SelectPlexServerRequest(BaseModel):
    machine_identifier: str = Field(min_length=1)


@app.put("/api/plex/server")
def select_plex_server(
    request: Request,
    body: SelectPlexServerRequest,
    response: Response,
    store: RequestStore = Depends(get_store),
    session: SessionRow | None = Depends(require_admin_or_setup_bootstrap),
) -> dict:
    """Finalizes the setup wizard's server picker, or (post-setup) an
    admin switching to a different owned server. Either way, re-resolves
    fresh connection details for the chosen server (a connection URL can
    change) rather than trusting whatever list_plex_servers last returned.

    `session` is None exactly when this call is what's finishing bootstrap
    (require_admin_or_setup_bootstrap's own bootstrap branch) — in that
    case this is also the moment the just-linked account should actually
    become signed in, not just "the server now knows who the admin is."
    Without this, completing setup would leave the admin having to run a
    *second*, separate PIN sign-in immediately after the one they just
    did to link the server in the first place, which is confusing on top
    of "why did that already work."""
    client, token, resources = _linked_account_resources(store)
    match = next(
        (r for r in resources if r["owned"] and r["machine_identifier"] == body.machine_identifier), None
    )
    if match is None:
        raise HTTPException(status_code=404, detail="server not found among this account's owned servers")
    store.update_settings(
        {
            "plex_server_url": match["url"],
            "plex_server_token": match["token"],
            "plex_server_name": match["name"],
            "plex_server_machine_id": match["machine_identifier"],
        }
    )
    # Switching servers changes who's authorized — every non-admin
    # session's access grant was checked against the *old* server and
    # must be re-validated via a fresh login against the new one.
    store.delete_non_admin_sessions()
    store.record_auth_event(
        "plex_server_selected",
        ip_address=_client_ip(request),
        detail=f"linked to {match['name']}",
    )

    if session is None:
        try:
            identity = client.get_account_identity(token)
        except Exception:  # fail safe: setup itself already succeeded above regardless of this
            identity = None
        if identity and identity.get("id"):
            plex_user_id = str(identity["id"])
            user = store.upsert_user(plex_user_id, identity.get("username"), True, identity.get("thumb"))
            session_id = secrets.token_urlsafe(32)
            store.create_session(session_id, user.plex_user_id, user.username, True, _new_session_expiry())
            _set_session_cookie(response, request, session_id)
    return {"server_name": match["name"]}


_ON_DECK_MAX = 12
_show_tmdb_cache: dict[str, tuple[float, int | None]] = {}
_SHOW_TMDB_TTL_SECONDS = 3600


def _tmdb_id_from_guids(item: dict | None) -> int | None:
    for guid in (item or {}).get("Guid", []) or []:
        value = guid.get("id", "")
        if value.startswith("tmdb://"):
            try:
                return int(value[len("tmdb://") :])
            except ValueError:
                return None
    return None


def _show_tmdb_id(client: PlexClient, url: str, token: str, rating_key: str | None) -> int | None:
    """TMDB id for one library item via its own metadata (cached an hour
    on success; a miss is retried next time, since Plex may still be
    matching a fresh item)."""
    if not rating_key:
        return None
    now = time.monotonic()
    cached = _show_tmdb_cache.get(rating_key)
    if cached and now - cached[0] < _SHOW_TMDB_TTL_SECONDS:
        return cached[1]
    try:
        tmdb_id = _tmdb_id_from_guids(client.metadata(url, token, rating_key))
    except Exception:  # noqa: BLE001 — transport errors just mean "unknown for now"
        return None
    if tmdb_id is not None:
        _show_tmdb_cache[rating_key] = (now, tmdb_id)
    return tmdb_id


_ON_DECK_LOOKUP_BUDGET_SECONDS = 6.0


def _resolve_tmdb_ids(client: PlexClient, url: str, token: str, rating_keys: set[str]) -> dict[str, int | None]:
    """Metadata lookups for several items at once, under one time budget
    — a slow Plex server degrades to "no link" for the stragglers rather
    than stalling the Home page."""
    results: dict[str, int | None] = {}
    if not rating_keys:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_show_tmdb_id, client, url, token, key): key for key in rating_keys}
        done, _ = concurrent.futures.wait(futures, timeout=_ON_DECK_LOOKUP_BUDGET_SECONDS)
        for future in done:
            try:
                results[futures[future]] = future.result()
            except Exception:  # noqa: BLE001
                results[futures[future]] = None
        pool.shutdown(wait=False, cancel_futures=True)
    return results


@router.get("/api/plex/on-deck")
def get_plex_on_deck(
    store: RequestStore = Depends(get_store),
    session: SessionRow = Depends(require_session),
) -> dict:
    """Plex's Continue Watching *for whoever is signed in*, shaped for the
    Home row: progress fraction, minutes left, the TMDB id (so a card can
    open this app's own detail page) and a same-origin artwork URL. Degrades
    to `available: false` rather than erroring — it backs a Home row, not
    a page anyone navigated to on purpose.

    Asked with that person's own server token, because /library/onDeck
    answers for whoever's token asked. Built on the admin's token — which is
    what settings holds and what this used — it showed the admin's viewing
    to the entire household: everyone's Home row was one person's half-watched
    episodes, which is both wrong and a small privacy leak between members.

    The token is captured during the access check every sign-in already makes
    (app/plex.py's check_server_access), so it arrives without an extra round
    trip. Someone whose session predates that has none stored yet; rather
    than fall back to the admin's and quietly reintroduce the bug for the
    people it most affects, the row simply stays empty until they next sign
    in. The admin keeps the settings token as a fallback because for the
    admin it is the same account's token — the same answer either way, so
    nothing regresses while the household cycles through."""
    settings = store.get_settings()
    url = settings.get("plex_server_url")
    token = store.get_user_server_token(session.plex_user_id)
    if not token and session.is_admin:
        token = settings.get("plex_server_token")
    if not url or not token:
        return {"available": False, "items": []}
    client = client_from_settings(settings)
    try:
        raw = client.on_deck(url, token)
    except Exception as exc:  # noqa: BLE001 — any transport failure degrades the row, never the page
        logger.info("plex on-deck unavailable: %s", exc)
        return {"available": False, "items": []}
    entries = [e for e in raw[: _ON_DECK_MAX] if e.get("type") in ("movie", "episode")]
    # Ids the listing didn't carry: a show's (for an episode) or a movie's
    # own when includeGuids gave nothing — fetched together, bounded.
    lookup_keys = {
        str(e.get("grandparentRatingKey") if e.get("type") == "episode" else e.get("ratingKey"))
        for e in entries
        if e.get("type") == "episode" or _tmdb_id_from_guids(e) is None
    }
    resolved = _resolve_tmdb_ids(client, url, token, lookup_keys)
    items = []
    for entry in entries:
        kind = entry.get("type")
        duration = entry.get("duration") or 0
        offset = entry.get("viewOffset") or 0
        progress = round(min(max(offset / duration, 0.0), 1.0), 3) if duration else 0.0
        if kind == "episode":
            tmdb_id = resolved.get(str(entry.get("grandparentRatingKey")))
            art = entry.get("thumb") or entry.get("art") or entry.get("grandparentArt")
        else:
            tmdb_id = _tmdb_id_from_guids(entry) or resolved.get(str(entry.get("ratingKey")))
            art = entry.get("art") or entry.get("thumb")
        items.append(
            {
                "rating_key": str(entry.get("ratingKey")),
                "type": kind,
                "media_type": "tv" if kind == "episode" else "movie",
                "title": entry.get("title"),
                "show_title": entry.get("grandparentTitle"),
                "season_number": entry.get("parentIndex"),
                "episode_number": entry.get("index"),
                "year": entry.get("year"),
                "progress": progress,
                "remaining_minutes": max(0, round((duration - offset) / 60000)) if duration else None,
                "tmdb_id": tmdb_id,
                "art_url": f"/api/plex/image?path={art}" if art else None,
            }
        )
    return {"available": True, "machine_id": settings.get("plex_server_machine_id"), "items": items}


@router.get("/api/plex/locate")
def plex_locate(
    type: str, title: str, year: int | None = None, tmdb_id: int | None = None, store: RequestStore = Depends(get_store)
) -> dict:
    """Where a title lives on the linked server, for the detail page's
    Play button: {available, rating_key, machine_id}. Degrades to
    available: false rather than erroring."""
    if type not in ("movie", "show"):
        raise HTTPException(status_code=400, detail="type must be movie or show")
    settings = store.get_settings()
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        return {"available": False}
    client = client_from_settings(settings)
    try:
        found = locate_title(store, client, type, title, year, tmdb_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("plex locate unavailable: %s", exc)
        return {"available": False}
    if not found:
        return {"available": False}
    return {"available": True, "machine_id": settings.get("plex_server_machine_id"), **found}


@router.get("/api/plex/recently-added")
def get_plex_recently_added(store: RequestStore = Depends(get_store)) -> dict:
    """Plex's Recently Added, shaped for the Home row: one card per movie
    or show (a season or episode collapses onto its show), the TMDB id
    when it can be resolved, and a same-origin poster URL. Degrades to
    `available: false` like the on-deck route."""
    settings = store.get_settings()
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        return {"available": False, "items": []}
    client = client_from_settings(settings)
    try:
        raw = client.recently_added(url, token)
    except Exception as exc:  # noqa: BLE001
        logger.info("plex recently-added unavailable: %s", exc)
        return {"available": False, "items": []}
    entries = [e for e in raw if e.get("type") in ("movie", "show", "season", "episode")]

    def show_key(e: dict) -> str | None:
        if e.get("type") == "season":
            return str(e.get("parentRatingKey"))
        if e.get("type") == "episode":
            return str(e.get("grandparentRatingKey"))
        return None

    lookup_keys = {show_key(e) or str(e.get("ratingKey")) for e in entries if show_key(e) or _tmdb_id_from_guids(e) is None}
    resolved = _resolve_tmdb_ids(client, url, token, {k for k in lookup_keys if k and k != "None"})
    items: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        kind = entry.get("type")
        key = show_key(entry) or str(entry.get("ratingKey"))
        if key in seen:
            continue
        seen.add(key)
        if kind == "season":
            title, year, poster = entry.get("parentTitle"), entry.get("parentYear"), entry.get("parentThumb")
        elif kind == "episode":
            title, year, poster = entry.get("grandparentTitle"), None, entry.get("grandparentThumb")
        else:
            title, year, poster = entry.get("title"), entry.get("year"), entry.get("thumb")
        tmdb_id = resolved.get(key) if show_key(entry) else (_tmdb_id_from_guids(entry) or resolved.get(key))
        items.append(
            {
                "rating_key": key,
                "media_type": "movie" if kind == "movie" else "tv",
                "title": title,
                "year": year,
                "added_at": entry.get("addedAt"),
                "tmdb_id": tmdb_id,
                "poster_url": f"/api/plex/image?path={poster}&width=300&height=450" if poster else None,
            }
        )
    return {"available": True, "items": items}


@router.get("/api/plex/image")
def get_plex_image(path: str, width: int = 640, height: int = 360, store: RequestStore = Depends(get_store)) -> RawResponse:
    """Same-origin proxy for Plex library artwork — see PlexClient.fetch_image."""
    if not path.startswith("/library/") or ".." in path:
        raise HTTPException(status_code=400, detail="not a library image path")
    settings = store.get_settings()
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        raise HTTPException(status_code=404, detail="Plex is not linked")
    client = client_from_settings(settings)
    try:
        content, content_type = client.fetch_image(url, token, path, min(width, 1280), min(height, 720))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Plex image unavailable: {exc}") from exc
    return RawResponse(content=content, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})
