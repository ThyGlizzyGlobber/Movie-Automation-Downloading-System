"""plex.tv and a Plex server, over HTTP: the PIN calls, an account's
servers and its access to each, a server's library index, and the client
identifier this app presents itself with."""

import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from app.matching.normalize import normalize_text, titles_match


PLEX_TV_BASE = "https://plex.tv"


PRODUCT_NAME = "Obsidian"


PIN_POLL_INTERVAL_SECONDS = 2


PIN_TIMEOUT_SECONDS = 900  # Plex PINs expire ~15 minutes after creation


YEAR_TOLERANCE = 1


# Plex's /library/all `type` param: 1=movie, 2=show (3=season, 4=episode,
# unused here). Stage 14's grid-wide "On Plex" badge.
_LIBRARY_TYPE = {"movie": 1, "show": 2}


class PlexError(RuntimeError):
    pass


def _tmdb_id_of(item: dict) -> int | None:
    """The TMDB id Plex's agent tagged an item with (`tmdb://617126` in
    its Guid list), or None for an item matched by an older agent."""
    for guid in item.get("Guid") or []:
        value = str(guid.get("id") or "")
        if value.startswith("tmdb://") and value[7:].isdigit():
            return int(value[7:])
    return None


def _matches(item: dict, target: str, year: int | None, tmdb_id: int | None, normalized: str | None = None) -> bool:
    """Whether a Plex item is the title asked about. When both sides
    carry a TMDB id, the ids decide on their own: titles collide ("Runner"
    against "The Runner") and drift ("The Fantastic 4: First Steps" on
    TMDB is "The Fantastic Four: First Steps" in Plex). Otherwise it's the
    title (see titles_match) within YEAR_TOLERANCE. `normalized` is the
    item's title already through normalize_text, when the caller has it."""
    item_id = item.get("tmdb_id", _tmdb_id_of(item))
    if tmdb_id is not None and item_id is not None:
        return item_id == tmdb_id
    if normalized is None:
        normalized = normalize_text(item.get("title", ""))
    if not titles_match(normalized, target):
        return False
    return not (year and item.get("year") and abs(item["year"] - year) > YEAR_TOLERANCE)


class LibraryIndex:
    """One library section's items ({title, year, tmdb_id, rating_key}),
    looked up by TMDB id first and by title after."""

    def __init__(self, items: list[dict]):
        self.items = items
        self._by_id = {i["tmdb_id"]: i for i in items if i.get("tmdb_id") is not None}
        self._by_title: dict[str, list[dict]] = {}
        # Every item's title normalised once, here. The fuzzy fallback in
        # find() walks the whole library for any title that isn't in it —
        # the usual case for a browse row — and normalising each library
        # title again on every lookup was most of what an "On Plex" badge
        # cost: ~2s to mark a 300-title landing page against 317 films.
        self._normalized: list[tuple[str, dict]] = []
        for item in items:
            normalized = normalize_text(item.get("title", ""))
            self._by_title.setdefault(normalized, []).append(item)
            self._normalized.append((normalized, item))

    def find(self, title: str, year: int | None, tmdb_id: int | None = None) -> dict | None:
        if tmdb_id is not None and tmdb_id in self._by_id:
            return self._by_id[tmdb_id]
        target = normalize_text(title)
        # Exact title first (the common case), then the fuzzy fallback.
        for item in self._by_title.get(target, []):
            if _matches(item, target, year, tmdb_id, target):
                return item
        for normalized, item in self._normalized:
            if _matches(item, target, year, tmdb_id, normalized):
                return item
        return None


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

    def auth_url(self, code: str, forward_url: str | None = None) -> str:
        """`forward_url` is plex.tv's own return trip: once the PIN is
        authorised it sends the browser straight back there, the way any
        OAuth provider returns to its redirect URI. Without it plex.tv
        just tells the user to "return to the app" by hand, which is the
        step that used to lose people — see LoginSession for what that
        cost. Optional because the caller can only supply it when it
        knows the browser's own origin (api/auth.py's `_return_url`)."""
        params = {
            "clientID": self.client_id,
            "code": code,
            "context[device][product]": PRODUCT_NAME,
        }
        if forward_url:
            params["forwardUrl"] = forward_url
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
        *or* shared with them. Backs the frontend migration's end-user
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

    def claim_token(self, token: str) -> str:
        """A one-time token that lets a new Plex server claim itself for
        this account (box/plex.py). Good for a few minutes."""
        response = self.session.get(
            f"{PLEX_TV_BASE}/api/claim/token.json", headers=self._headers(token), timeout=10
        )
        if not response.ok:
            raise PlexError(f"couldn't get a Plex claim token: {response.status_code}")
        return response.json()["token"]

    def get_account_identity(self, token: str) -> dict | None:
        """The signed-in account's own stable plex.tv id and username —
        who is this, as opposed to which server to link. Same /api/v2/user endpoint get_account_username already
        uses; None if the token is no longer valid."""
        response = self.session.get(f"{PLEX_TV_BASE}/api/v2/user", headers=self._headers(token), timeout=10)
        if not response.ok:
            return None
        data = response.json()
        # `thumb` is the account's avatar on plex.tv; its query string is
        # only a cache-buster, the bare URL always serves the current one.
        thumb = (data.get("thumb") or "").split("?")[0] or None
        return {"id": data.get("id"), "username": data.get("username") or data.get("title"), "thumb": thumb}

    def access_by_server(self, token: str) -> dict[str, dict]:
        """Every Plex server this account can reach — owned or shared —
        keyed by machine id, as `{"owned": bool, "token": str}`: one
        plex.tv call however many servers a sign-in is checked against.
        A server of this app's missing from it is a hard refusal for
        end-user login on that server (frontend migration Part C3);
        `owned` is what distinguishes the admin from every other
        authorized user, and `token` is this account's *own* access token
        for that server.

        The token was once fetched and thrown away here. It is what makes
        a per-user Plex read possible at all: /library/onDeck answers for
        whoever's token asked, so Continue Watching built on the admin's
        token shows the admin's viewing to the whole household, no matter
        who is signed in. This is the only moment it can be captured — it
        comes back with the access check that every sign-in already
        performs."""
        return {
            r["machine_identifier"]: {"owned": r["owned"], "token": r["token"]} for r in self.list_resources(token)
        }

    def has_movie(self, server_url: str, server_token: str, title: str, year: int | None, tmdb_id: int | None = None) -> bool:
        """True if a movie matching `title` (and `year`, within a year of
        tolerance) already exists in this Plex server's library — searched
        with /library/all rather than enumerating sections first."""
        response = self.session.get(
            f"{server_url}/library/all",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            params={"type": 1, "title": title, "includeGuids": 1},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex library search failed: {response.status_code}")
        items = response.json().get("MediaContainer", {}).get("Metadata", []) or []
        target = normalize_text(title)
        return any(_matches(item, target, year, tmdb_id) for item in items)

    def on_deck(self, server_url: str, server_token: str) -> list[dict]:
        """Plex's own "Continue Watching" list for the linked server —
        `/library/onDeck`, every in-progress movie/episode with its
        `viewOffset`/`duration`. Backs the Home page's Continue watching
        row. Raw Metadata dicts; api/plex.py shapes them."""
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

    def watched(self, server_url: str, server_token: str, media_type: str, limit: int = 300) -> list[dict]:
        """What the token's own account has watched in one library type
        ("movie"/"show"), most recent first: `{tmdb_id, title, year,
        last_viewed_at}` with `last_viewed_at` an ISO timestamp.

        Watched state is per Plex account, so this answers for whoever's
        token asks — the same reason on_deck is asked with each person's
        own token. `viewCount>>=1` is Plex's "at least once" filter; a show
        counts as soon as any episode has been played. Items the agent
        never matched to TMDB are skipped: without an id they can't seed
        anything."""
        response = self.session.get(
            f"{server_url}/library/all",
            headers={
                "Accept": "application/json",
                "X-Plex-Token": server_token,
                "X-Plex-Container-Start": "0",
                "X-Plex-Container-Size": str(limit),
            },
            params={"type": _LIBRARY_TYPE[media_type], "includeGuids": 1, "sort": "lastViewedAt:desc", "viewCount>>": 0},
            timeout=15,
        )
        if not response.ok:
            raise PlexError(f"Plex watch history fetch failed: {response.status_code}")
        out = []
        for item in response.json().get("MediaContainer", {}).get("Metadata", []) or []:
            tmdb_id = _tmdb_id_of(item)
            # Checked here as well as filtered for: a server that ignored
            # the filter would otherwise hand back the whole library as
            # "watched".
            if tmdb_id is None or not (item.get("viewCount") or item.get("viewedLeafCount")):
                continue
            stamp = item.get("lastViewedAt")
            out.append(
                {
                    "tmdb_id": tmdb_id,
                    "title": item.get("title") or "",
                    "year": item.get("year"),
                    "last_viewed_at": datetime.fromtimestamp(int(stamp), timezone.utc).isoformat() if stamp else None,
                }
            )
        return out

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

    def locate(
        self, server_url: str, server_token: str, media_type: str, title: str, year: int | None, tmdb_id: int | None = None
    ) -> dict | None:
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
            if not _matches(item, target, year, tmdb_id):
                continue
            exact = normalize_text(item.get("title", "")) == target or (tmdb_id is not None and _tmdb_id_of(item) == tmdb_id)
            if best is None or (exact and not best[0]):
                best = (exact, item)
        if best is None:
            return None
        item = best[1]
        return {"rating_key": str(item.get("ratingKey")), "title": item.get("title"), "year": item.get("year")}

    def file_paths(self, server_url: str, server_token: str, rating_key: str) -> list[str]:
        """The media file paths Plex has for an item, as Plex sees them."""
        response = self.session.get(
            f"{server_url}/library/metadata/{rating_key}",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            timeout=10,
        )
        if not response.ok:
            raise PlexError(f"Plex metadata fetch failed: {response.status_code}")
        items = response.json().get("MediaContainer", {}).get("Metadata", []) or []
        paths: list[str] = []
        for item in items:
            for media in item.get("Media", []) or []:
                for part in media.get("Part", []) or []:
                    if part.get("file"):
                        paths.append(part["file"])
        return paths

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

    def section_items(self, server_url: str, server_token: str, section_key: str, item_type: int) -> list[dict]:
        """Every item of one type in a section — `1` movies, `4`
        episodes. Paged, because a library of any size exceeds whatever
        Plex feels like returning in one go."""
        out: list[dict] = []
        start, page = 0, 500
        while True:
            response = self.session.get(
                f"{server_url}/library/sections/{section_key}/all",
                headers={
                    "Accept": "application/json",
                    "X-Plex-Token": server_token,
                    "X-Plex-Container-Start": str(start),
                    "X-Plex-Container-Size": str(page),
                },
                params={"type": item_type},
                timeout=30,
            )
            if not response.ok:
                raise PlexError(f"Plex section listing failed: {response.status_code}")
            batch = response.json().get("MediaContainer", {}).get("Metadata", []) or []
            out += batch
            if len(batch) < page:
                return out
            start += page

    def select_audio_stream(self, server_url: str, server_token: str, part_id: str, stream_id: str) -> None:
        """Tells Plex which audio track this part should use.

        The file's own default flag only decides Plex's *first* choice.
        Plex records what it picked when the item was scanned and keeps
        it — neither Refresh Metadata nor Analyze revisits it — so a
        default flag corrected afterwards changes nothing on its own.
        Confirmed live 2026-09-27 on Supernatural s14e01: Plex reported
        the English track as `default` and the Italian one as
        `selected`, which is exactly the split this closes.

        This is the same call Plex Web makes when someone picks a track
        from an item's page rather than mid-playback, and it is stored
        against the part rather than against a viewer, which is what
        makes it reach everyone.
        """
        response = self.session.put(
            f"{server_url}/library/parts/{part_id}",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            params={"audioStreamID": stream_id, "allParts": "1"},
            timeout=15,
        )
        if not response.ok:
            raise PlexError(f"Plex audio-track selection failed: {response.status_code}")

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

    def library_index(self, server_url: str, server_token: str, media_type: str) -> LibraryIndex:
        """Every title in one whole library section (`media_type`
        "movie"/"show"), with its year, TMDB id and rating key — one bulk
        `/library/all` call (no title filter) rather than the N per-item
        calls `has_movie` makes, so a discover/search grid of 20-40 items
        can check "is this on Plex" against one fetch instead of 20-40 live
        requests. See `plex_library_lookup` below for the caller-facing,
        cached version of this."""
        response = self.session.get(
            f"{server_url}/library/all",
            headers={"Accept": "application/json", "X-Plex-Token": server_token},
            params={"type": _LIBRARY_TYPE[media_type], "includeGuids": 1},
            timeout=15,
        )
        if not response.ok:
            raise PlexError(f"Plex library fetch failed: {response.status_code}")
        items = response.json().get("MediaContainer", {}).get("Metadata", []) or []
        return LibraryIndex(
            [
                {
                    "title": item.get("title", ""),
                    "year": item.get("year"),
                    "tmdb_id": _tmdb_id_of(item),
                    "rating_key": str(item.get("ratingKey")) if item.get("ratingKey") is not None else None,
                }
                for item in items
                if normalize_text(item.get("title", ""))
            ]
        )


def client_from_settings(settings: dict) -> PlexClient:
    """A client for server reads, under the saved client id — or a
    throwaway one, not persisted, if no sign-in has saved one yet."""
    return PlexClient(settings.get("plex_client_id") or new_client_identifier())


def persisted_client(store) -> PlexClient:
    """A client for plex.tv sign-in, whose client id has to stay the same
    across requests: generated and saved on first use."""
    client_id = store.get_settings().get("plex_client_id")
    if not client_id:
        client_id = new_client_identifier()
        store.update_settings({"plex_client_id": client_id})
    return PlexClient(client_id)
