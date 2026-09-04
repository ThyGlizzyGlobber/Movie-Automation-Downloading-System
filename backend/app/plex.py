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
from urllib.parse import urlencode

import requests

from app.normalize import normalize_text

PLEX_TV_BASE = "https://plex.tv"
PRODUCT_NAME = "The Family Downloader"
PIN_POLL_INTERVAL_SECONDS = 2
PIN_TIMEOUT_SECONDS = 900  # Plex PINs expire ~15 minutes after creation
YEAR_TOLERANCE = 1


class PlexError(RuntimeError):
    pass


def new_client_identifier() -> str:
    """A stable per-installation id Plex uses to recognize this app across
    requests — generated once and persisted in settings, not a secret."""
    return str(uuid.uuid4())


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

    def get_owned_server(self, token: str) -> dict | None:
        """The first Plex Media Server this account owns, with a usable
        connection URL and its own resource-level access token (what a PMS
        actually expects, distinct from the plex.tv account token)."""
        response = self.session.get(
            f"{PLEX_TV_BASE}/api/v2/resources", headers=self._headers(token), params={"includeHttps": "1"}, timeout=10
        )
        if not response.ok:
            raise PlexError(f"couldn't list Plex servers: {response.status_code}")
        for resource in response.json():
            if "server" not in (resource.get("provides") or "").split(","):
                continue
            if not resource.get("owned"):
                continue
            connections = resource.get("connections") or []
            local = next((c for c in connections if c.get("local") and not c.get("relay")), None)
            chosen = local or (connections[0] if connections else None)
            if not chosen:
                continue
            return {
                "name": resource.get("name"),
                "url": chosen["uri"],
                "token": resource.get("accessToken") or token,
            }
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
            if normalize_text(item.get("title", "")) != target:
                continue
            if year and item.get("year") and abs(item["year"] - year) > YEAR_TOLERANCE:
                continue
            return True
        return False


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
                    server = await asyncio.to_thread(client.get_owned_server, token)
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
            }
        )
