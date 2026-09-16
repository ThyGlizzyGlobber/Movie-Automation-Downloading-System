"""Plex account linking (PIN-based sign-in, the same device-linking flow
Plex's own apps use — no client secret, no server-side OAuth callback) and
a library lookup used only to confirm a download actually completed once
qBittorrent's own copy of the torrent has already vanished (e.g. "remove
torrent after completion" enabled in qBittorrent). Per "fail safe, not
best guess": a disappearance is only ever called Cancelled once Plex has
also failed to find the title — see worker.py's `_check_downloading`.

The resulting Plex account token never reaches the browser — same rule as
the TMDB key and qBittorrent credentials. It's persisted server-side in
the settings table (db.py) alongside the per-server access token."""

import asyncio
import time
import uuid
from typing import Callable
from urllib.parse import urlencode

import logging
import requests

from app.cache import TTLCache
from app.normalize import normalize_text, titles_match

PLEX_TV_BASE = "https://plex.tv"
PRODUCT_NAME = "Meridian"
PIN_POLL_INTERVAL_SECONDS = 2
PIN_TIMEOUT_SECONDS = 900  # Plex PINs expire ~15 minutes after creation
YEAR_TOLERANCE = 1

# Plex's /library/all `type` param: 1=movie, 2=show (3=season, 4=episode,
# unused here). Stage 14's grid-wide "On Plex" badge.
_LIBRARY_TYPE = {"movie": 1, "show": 2}


class PlexError(RuntimeError):
    pass


def new_client_identifier() -> str:
    """A stable per-installation id Plex uses to recognize this app across
    requests — generated once and persisted in settings, not a secret."""
    return str(uuid.uuid4())


logger = logging.getLogger(__name__)


class PlexClient:
    def __init__(self, client_id: str, session: requests.Session | None = None):
        self.client_id = client_id
        self.session = session or requests.Session()

    def _headers(self, token: str | None = None) -> dict:
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": PRODUCT_NAME,
            "X-Plex-Client-Identifier": self.client_id,
        }
        if token:
            headers["X-Plex-Token"] = token
        return headers

    def create_pin(self) -> dict:
        response = self.session.post(
            f"{PLEX_TV_BASE}/api/v2/pins", headers=self._headers(), data={"strong": "true"}, timeout=10
        )
        if not response.ok:
            raise PlexError(f"couldn't start Plex sign-in: {response.status_code}")
        data = response.json()
        return {"id": data["id"], "code": data["code"]}

    def auth_url(self, code: str) -> str:
        params = {
            "clientID": self.client_id,
            "code": code,
            "context[device][product]": PRODUCT_NAME,
        }
        return f"https://app.plex.tv/auth#?{urlencode(params)}"

    def check_pin(self, pin_id: int) -> str | None:
        """The account token once the user finishes signing in at
        `auth_url`, or None while the PIN is still waiting."""
        response = self.session.get(f"{PLEX_TV_BASE}/api/v2/pins/{pin_id}", headers=self._headers(), timeout=10)
        if not response.ok:
            raise PlexError(f"couldn't check Plex sign-in status: {response.status_code}")
        return response.json().get("authToken") or None

    def get_account_username(self, token: str) -> str | None:
        response = self.session.get(f"{PLEX_TV_BASE}/api/v2/user", headers=self._headers(token), timeout=10)
        if not response.ok:
            return None
        data = response.json()
        return data.get("username") or data.get("title")

    def list_resources(self, token: str) -> list[dict]:
        """Every Plex Media Server resource this account can see — owned
        *or* shared with them, unlike get_owned_server below, which stops
        at the first owned one. Backs the frontend migration's end-user
        access check (does this account have access to *our* server —
        Part C1) and the admin's own multi-server picker (a person can
        own more than one Plex server, Part C1's "linking is a picker, not
        take the first result"). A user's own `/api/v2/resources` call
        includes servers shared with them, not just ones they own — the
        same mechanism Plex's own apps use to populate a "select server"
        screen, confirmed against Plex community/forum docs during the
        migration plan's design pass."""
        response = self.session.get(
            f"{PLEX_TV_BASE}/api/v2/resources", headers=self._headers(token), params={"includeHttps": "1"}, timeout=10
        )
        if not response.ok:
            raise PlexError(f"couldn't list Plex servers: {response.status_code}")
        resources = []
        for resource in response.json():
            if "server" not in (resource.get("provides") or "").split(","):
                continue
            connections = resource.get("connections") or []
            local = next((c for c in connections if c.get("local") and not c.get("relay")), None)
            chosen = local or (connections[0] if connections else None)
            if not chosen:
                continue
            resources.append(
                {
                    "name": resource.get("name"),
                    "url": chosen["uri"],
                    "token": resource.get("accessToken") or token,
                    "owned": bool(resource.get("owned")),
                    "machine_identifier": resource.get("clientIdentifier"),
                }
            )
        return resources

    def get_owned_server(self, token: str) -> dict | None:
        """The first Plex Media Server this account owns, with a usable
        connection URL and its own resource-level access token (what a PMS
        actually expects, distinct from the plex.tv account token). A thin
        filter over list_resources — kept as its own method/return shape
        since it's still what admin unlink/relink-to-first-server callers
        want, and changing its shape would break existing callers."""
        for resource in self.list_resources(token):
            if resource["owned"]:
                return {"name": resource["name"], "url": resource["url"], "token": resource["token"]}
        return None

    def get_account_identity(self, token: str) -> dict | None:
        """The signed-in account's own stable plex.tv id and username —
        who is this, distinct from get_owned_server's server-linking
        concern. Same /api/v2/user endpoint get_account_username already
        uses; None if the token is no longer valid."""
        response = self.session.get(f"{PLEX_TV_BASE}/api/v2/user", headers=self._headers(token), timeout=10)
        if not response.ok:
            return None
        data = response.json()
        # `thumb` is the account's avatar on plex.tv; its query string is
        # only a cache-buster, the bare URL always serves the current one.
        thumb = (data.get("thumb") or "").split("?")[0] or None
        return {"id": data.get("id"), "username": data.get("username") or data.get("title"), "thumb": thumb}

    def check_server_access(self, token: str, machine_identifier: str) -> dict | None:
        """Whether this account has access — owned or shared — to the
        Plex server identified by `machine_identifier` (this app's own
        linked server, from settings' `plex_server_machine_id`). None
        means no access at all, which is a hard refusal for end-user
        login (frontend migration Part C3); otherwise `{"owned": bool}`,
        which is what distinguishes the admin from every other authorized
        user."""
        for resource in self.list_resources(token):
            if resource["machine_identifier"] == machine_identifier:
                return {"owned": resource["owned"]}
        return None

    def has_movie(self, server_url: str, server_token: str, title: str, year: int | None) -> bool:
        """True if a movie matching `title` (and `year`, within a year of
        tolerance) already exists in this Plex server's library — searched
        with /library/all rather than enumerating sections first."""
        response = self.session.get(
            f"{server_url}/library/all",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            params={"type": 1, "title": title},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex library search failed: {response.status_code}")
        items = response.json().get("MediaContainer", {}).get("Metadata", []) or []
        target = normalize_text(title)
        for item in items:
            if not titles_match(normalize_text(item.get("title", "")), target):
                continue
            if year and item.get("year") and abs(item["year"] - year) > YEAR_TOLERANCE:
                continue
            return True
        return False

    def on_deck(self, server_url: str, server_token: str) -> list[dict]:
        """Plex's own "Continue Watching" list for the linked server —
        `/library/onDeck`, every in-progress movie/episode with its
        `viewOffset`/`duration`. Backs the Home page's Continue watching
        row. Raw Metadata dicts; api.py shapes them."""
        response = self.session.get(
            f"{server_url}/library/onDeck",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            # includeGuids adds each item's external ids (tmdb://…) to the
            # listing itself, sparing a metadata round trip per movie.
            params={"includeGuids": 1},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex on-deck fetch failed: {response.status_code}")
        return response.json().get("MediaContainer", {}).get("Metadata", []) or []

    def recently_added(self, server_url: str, server_token: str, limit: int = 24) -> list[dict]:
        """Plex's own "Recently Added" for the linked server — backs the
        Home page's "New in your library" row. Raw Metadata dicts."""
        response = self.session.get(
            f"{server_url}/library/recentlyAdded",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            params={"includeGuids": 1, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex recently-added fetch failed: {response.status_code}")
        return response.json().get("MediaContainer", {}).get("Metadata", []) or []

    def locate(self, server_url: str, server_token: str, media_type: str, title: str, year: int | None) -> dict | None:
        """The library item for a title (and year, when known): a filtered
        `/library/all` search, then the same title/year matching the
        on-Plex badge uses, so the detail page's Play button can open the
        exact item in Plex Web. None when nothing matches."""
        response = self.session.get(
            f"{server_url}/library/all",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            params={"type": _LIBRARY_TYPE[media_type], "title": title, "includeGuids": 1},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex search failed: {response.status_code}")
        target = normalize_text(title)
        best = None
        for item in response.json().get("MediaContainer", {}).get("Metadata", []) or []:
            if not titles_match(normalize_text(item.get("title", "")), target):
                continue
            if year and item.get("year") and abs(item["year"] - year) > YEAR_TOLERANCE:
                continue
            exact = normalize_text(item.get("title", "")) == target
            if best is None or (exact and not best[0]):
                best = (exact, item)
        if best is None:
            return None
        item = best[1]
        return {"rating_key": str(item.get("ratingKey")), "title": item.get("title"), "year": item.get("year")}

    def leaf_count(self, server_url: str, server_token: str, rating_key: str) -> int | None:
        """How many episodes the server holds for a show (Plex's own
        `leafCount` on the show item), or None when it can't say."""
        response = self.session.get(
            f"{server_url}/library/metadata/{rating_key}",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex metadata fetch failed: {response.status_code}")
        items = response.json().get("MediaContainer", {}).get("Metadata", []) or []
        if not items:
            return None
        count = items[0].get("leafCount")
        return int(count) if count is not None else None

    def show_episodes(self, server_url: str, server_token: str, rating_key: str) -> set[tuple[int, int]]:
        """Every episode the server holds for a show, as (season, episode)
        pairs — Plex's `allLeaves` listing, `parentIndex`/`index`."""
        response = self.session.get(
            f"{server_url}/library/metadata/{rating_key}/allLeaves",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            timeout=15,
        )
        if not response.ok:
            raise PlexError(f"Plex episode listing failed: {response.status_code}")
        out: set[tuple[int, int]] = set()
        for item in response.json().get("MediaContainer", {}).get("Metadata", []) or []:
            season, episode = item.get("parentIndex"), item.get("index")
            if season is not None and episode is not None:
                out.add((int(season), int(episode)))
        return out

    def sections(self, server_url: str, server_token: str) -> list[dict]:
        """The server's libraries: [{key, type ('movie'|'show'), title,
        locations: [paths]}], for the after-import refresh."""
        response = self.session.get(
            f"{server_url}/library/sections",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex sections fetch failed: {response.status_code}")
        out = []
        for d in response.json().get("MediaContainer", {}).get("Directory", []) or []:
            out.append(
                {
                    "key": str(d.get("key")),
                    "type": d.get("type"),
                    "title": d.get("title"),
                    "locations": [loc.get("path") for loc in d.get("Location", []) or [] if loc.get("path")],
                }
            )
        return out

    def refresh_section(self, server_url: str, server_token: str, section_key: str, path: str | None = None) -> None:
        """Asks Plex to scan one library — the whole section, or only
        `path` when it lies inside one of the section's locations."""
        params = {"path": path} if path else None
        response = self.session.get(
            f"{server_url}/library/sections/{section_key}/refresh",
            headers={"X-Plex-Token": server_token},
            params=params,
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex refresh failed: {response.status_code}")

    def metadata(self, server_url: str, server_token: str, rating_key: str) -> dict | None:
        """One library item by rating key (used to read a show's external
        ids for an on-deck episode, which only carries its own)."""
        response = self.session.get(
            f"{server_url}/library/metadata/{rating_key}",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            timeout=5,
        )
        if not response.ok:
            raise PlexError(f"Plex metadata fetch failed: {response.status_code}")
        items = response.json().get("MediaContainer", {}).get("Metadata", []) or []
        return items[0] if items else None

    def fetch_image(self, server_url: str, server_token: str, path: str, width: int, height: int) -> tuple[bytes, str]:
        """A library image (poster/art/still) resized by the server's own
        photo transcoder, so the frontend can show Plex artwork through
        this app's origin (the CSP allows no other image host) without
        ever handing the server token to the browser."""
        response = self.session.get(
            f"{server_url}/photo/:/transcode",
            headers={"X-Plex-Token": server_token},
            params={"width": width, "height": height, "minSize": 1, "upscale": 1, "url": path},
            timeout=15,
        )
        if not response.ok:
            raise PlexError(f"Plex image fetch failed: {response.status_code}")
        return response.content, response.headers.get("Content-Type", "image/jpeg")

    def library_index(self, server_url: str, server_token: str, media_type: str) -> dict[str, list[int | None]]:
        """Every title in one whole library section (`media_type`
        "movie"/"show"), as `normalize_text(title) -> [years...]` — one bulk
        `/library/all` call (no title filter) rather than the N per-item
        calls `has_movie` makes, so a discover/search grid of 20-40 items
        can check "is this on Plex" against one fetch instead of 20-40 live
        requests. See `plex_library_lookup` below for the caller-facing,
        cached version of this."""
        response = self.session.get(
            f"{server_url}/library/all",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            params={"type": _LIBRARY_TYPE[media_type]},
            timeout=15,
        )
        if not response.ok:
            raise PlexError(f"Plex library fetch failed: {response.status_code}")
        items = response.json().get("MediaContainer", {}).get("Metadata", []) or []
        index: dict[str, list[int | None]] = {}
        for item in items:
            key = normalize_text(item.get("title", ""))
            if not key:
                continue
            index.setdefault(key, []).append(item.get("year"))
        return index


# One cache per (server_url, server_token, media_type) — a fresh PlexClient
# is constructed per call below (matching has_in_library's existing
# pattern), so `app.cache.ttl_cache`'s self-keying decorator would never
# actually hit; this is keyed on the request's own identity instead.
_LIBRARY_INDEX_TTL_SECONDS = 120
_library_index_cache = TTLCache(_LIBRARY_INDEX_TTL_SECONDS)


def plex_library_lookup(store, media_type: str) -> Callable[[str, int | None], bool] | None:
    """Returns a matcher `(title, year) -> bool` backed by one cached,
    whole-library snapshot, or `None` if Plex isn't linked. Meant to be
    called once per API request (not once per item) so a whole page of
    discover/search results can be annotated with a single Plex round trip
    every ~2 minutes rather than one per item. Reads settings fresh each
    call (Plex can be linked/unlinked while the backend is running), same
    as `has_in_library`."""
    settings = store.get_settings()
    server_url = settings.get("plex_server_url")
    server_token = settings.get("plex_server_token")
    if not server_url or not server_token:
        return None

    cache_key = (server_url, server_token, media_type)
    index, hit = _library_index_cache.get(cache_key)
    if not hit:
        client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
        try:
            index = client.library_index(server_url, server_token, media_type)
        except PlexError:
            # Fail safe, not best guess: a Plex hiccup must not break
            # browsing — every item just reports "not on Plex" this request.
            index = {}
        _library_index_cache.set(cache_key, index)

    def matcher(title: str, year: int | None) -> bool:
        target = normalize_text(title)
        years = index.get(target)  # exact hit — O(1), the common case
        if years is None:
            # Fuzzy fallback (see titles_match): a franchise-prefixed Plex
            # title ("Star Wars: The Mandalorian and Grogu") wouldn't be an
            # exact key match against TMDB's plain title. O(library size),
            # only reached once no exact key exists, and only ever run
            # once per ~2 minutes per (server, media_type) thanks to the
            # cache above.
            years = [y for key, ys in index.items() if titles_match(target, key) for y in ys] or None
        if years is None:
            return False
        if year is None:
            return True
        return any(y is None or abs(y - year) <= YEAR_TOLERANCE for y in years)

    return matcher


def has_in_library(store, title: str, year: int | None) -> bool | None:
    """None if Plex isn't linked (caller falls back to its own default);
    True/False once we can actually check. Reads settings fresh each call
    rather than caching a client, since the household can link/unlink
    while the backend is running."""
    settings = store.get_settings()
    server_url = settings.get("plex_server_url")
    server_token = settings.get("plex_server_token")
    if not server_url or not server_token:
        return None
    client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
    return client.has_movie(server_url, server_token, title, year)


class PlexLinker:
    """Drives the PIN sign-in flow in the background: creates a PIN, hands
    the frontend a URL to open, then polls plex.tv itself until the
    browser-side login completes, resolves the account's owned server, and
    persists everything to settings. The frontend only ever polls this
    app's own `/api/plex/status` — see api.py."""

    def __init__(self, store):
        self.store = store
        self._task: asyncio.Task | None = None
        self._error: str | None = None

    def _client(self) -> PlexClient:
        settings = self.store.get_settings()
        client_id = settings.get("plex_client_id")
        if not client_id:
            client_id = new_client_identifier()
            self.store.update_settings({"plex_client_id": client_id})
        return PlexClient(client_id)

    async def start(self) -> str:
        client = self._client()
        self._error = None
        pin = await asyncio.to_thread(client.create_pin)
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._poll(client, pin["id"]))
        return client.auth_url(pin["code"])

    async def _poll(self, client: PlexClient, pin_id: int) -> None:
        deadline = time.monotonic() + PIN_TIMEOUT_SECONDS
        try:
            while time.monotonic() < deadline:
                token = await asyncio.to_thread(client.check_pin, pin_id)
                if token:
                    resources = await asyncio.to_thread(client.list_resources, token)
                    server = next((r for r in resources if r["owned"]), None)
                    if not server:
                        self._error = "Signed in, but no Plex server was found on this account."
                        return
                    username = await asyncio.to_thread(client.get_account_username, token)
                    await asyncio.to_thread(
                        self.store.update_settings,
                        {
                            "plex_token": token,
                            "plex_username": username,
                            "plex_server_url": server["url"],
                            "plex_server_token": server["token"],
                            "plex_server_name": server["name"],
                            # Frontend migration Part C1: what end-user
                            # access checks compare against — see
                            # check_server_access above.
                            "plex_server_machine_id": server["machine_identifier"],
                        },
                    )
                    return
                await asyncio.sleep(PIN_POLL_INTERVAL_SECONDS)
            self._error = "Plex sign-in timed out — try again."
        except Exception as exc:  # fail safe — never leave the frontend polling forever
            self._error = str(exc)

    def status(self) -> dict:
        settings = self.store.get_settings()
        linked = bool(settings.get("plex_token"))
        return {
            "linked": linked,
            "username": settings.get("plex_username") if linked else None,
            "server_name": settings.get("plex_server_name") if linked else None,
            "pending": bool(self._task and not self._task.done()),
            "error": self._error,
        }

    def unlink(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._error = None
        self.store.update_settings(
            {
                "plex_token": None,
                "plex_username": None,
                "plex_server_url": None,
                "plex_server_token": None,
                "plex_server_name": None,
                "plex_server_machine_id": None,
            }
        )
        # Unlinking removes the only server anyone's access was ever
        # checked against — every session (including the admin's own)
        # must re-authenticate rather than keep working against a server
        # that's no longer configured at all.
        self.store.delete_non_admin_sessions()


class LoginSession:
    """The end-user equivalent of PlexLinker's PIN sign-in flow (frontend
    migration Part C3) — same plex.tv PIN mechanism, different purpose:
    PlexLinker links *this app* to a Plex server (persisted, survives
    restarts); LoginSession authenticates *one person* against the
    already-linked server (a signed-in app session, not app-wide state).
    Deliberately in-memory only, unlike PlexLinker — an interrupted login
    attempt has nothing worth surviving a restart for, the user just
    starts over."""

    def __init__(self, store):
        self.store = store
        self._task: asyncio.Task | None = None
        self._result: dict | None = None
        self._error: str | None = None

    def _client(self) -> PlexClient:
        settings = self.store.get_settings()
        client_id = settings.get("plex_client_id")
        if not client_id:
            client_id = new_client_identifier()
            self.store.update_settings({"plex_client_id": client_id})
        return PlexClient(client_id)

    async def start(self) -> str:
        client = self._client()
        self._error = None
        self._result = None
        pin = await asyncio.to_thread(client.create_pin)
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._poll(client, pin["id"]))
        return client.auth_url(pin["code"])

    async def _poll(self, client: PlexClient, pin_id: int) -> None:
        deadline = time.monotonic() + PIN_TIMEOUT_SECONDS
        try:
            while time.monotonic() < deadline:
                token = await asyncio.to_thread(client.check_pin, pin_id)
                if token:
                    machine_id = self.store.get_settings().get("plex_server_machine_id")
                    if not machine_id:
                        self._error = "This server hasn't linked a Plex account yet."
                        return
                    access = await asyncio.to_thread(client.check_server_access, token, machine_id)
                    if access is None:
                        self._error = "Your Plex account doesn't have access to this server."
                        return
                    identity = await asyncio.to_thread(client.get_account_identity, token)
                    if not identity or not identity.get("id"):
                        self._error = "Couldn't verify the signed-in Plex account."
                        return
                    self._result = {
                        "plex_user_id": str(identity["id"]),
                        "username": identity.get("username"),
                        "is_admin": access["owned"],
                        "thumb": identity.get("thumb"),
                    }
                    return
                await asyncio.sleep(PIN_POLL_INTERVAL_SECONDS)
            self._error = "Plex sign-in timed out — try again."
        except Exception as exc:  # fail safe — never leave the frontend polling forever
            self._error = str(exc)

    def status(self) -> dict:
        return {
            "pending": bool(self._task and not self._task.done()),
            "result": self._result,
            "error": self._error,
        }


def refresh_after_import(store, media_type: str, organized_path: str | None) -> bool:
    """Settings › Plex "Refresh Plex after import": scans the movie or show
    library once a file has been placed. The organized path is passed as
    a partial scan when it sits under one of the section's own locations
    (Plex sees the same mount), otherwise the whole section is refreshed.
    Best effort — a failure is logged, never raised into the worker."""
    settings = store.get_settings()
    if settings.get("plex_refresh_after_import", True) is False:
        return False
    url, token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not url or not token:
        return False
    client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
    wanted = "movie" if media_type == "movie" else "show"
    try:
        sections = [s for s in client.sections(url, token) if s.get("type") == wanted]
        if not sections:
            return False
        for section in sections:
            partial = None
            if organized_path and any(organized_path.startswith(loc.rstrip("/") + "/") for loc in section["locations"]):
                partial = organized_path
            client.refresh_section(url, token, section["key"], partial)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("plex refresh after import skipped: %s", exc)
        return False


# Plex as the verifier of what a household actually has of a show: the
# worker's follow check and pack fallbacks ask this before requesting
# anything, so an episode already on the server is marked done rather
# than fetched again — whatever the request ledger says about it.
_show_episodes_cache: dict[tuple[str, str, int | None], tuple[float, set[tuple[int, int]] | None]] = {}
_SHOW_EPISODES_TTL_SECONDS = 60.0


def plex_show_episodes(store, title: str, year: int | None) -> set[tuple[int, int]] | None:
    """The (season, episode) pairs Plex holds for `title`, or None when
    Plex isn't linked or doesn't have the show at all. Cached a minute
    per show so a check that walks every season asks once."""
    settings = store.get_settings()
    server_url, server_token = settings.get("plex_server_url"), settings.get("plex_server_token")
    if not server_url or not server_token:
        return None
    key = (server_url, title, year)
    hit = _show_episodes_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _SHOW_EPISODES_TTL_SECONDS:
        return hit[1]
    client = PlexClient(settings.get("plex_client_id") or new_client_identifier())
    try:
        item = client.locate(server_url, server_token, "show", title, year)
        episodes = client.show_episodes(server_url, server_token, item["rating_key"]) if item else None
    except PlexError:
        episodes = None
    _show_episodes_cache[key] = (now, episodes)
    return episodes
