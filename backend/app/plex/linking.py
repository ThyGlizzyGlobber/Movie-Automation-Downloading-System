"""Signing in with Plex. PlexLinker links the admin's account and keeps
their servers (and, on a box, claims its own Plex first); LoginSession is
one person's own PIN sign-in, which is what mints their session."""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

from app.box import plex as box_plex
from app.plex.client import PIN_POLL_INTERVAL_SECONDS, PIN_TIMEOUT_SECONDS, PlexClient, persisted_client
from app.updater import channel

logger = logging.getLogger(__name__)


class PlexLinker:
    """Drives the PIN sign-in flow in the background: creates a PIN, hands
    the frontend a URL to open, then polls plex.tv itself until the
    browser-side login completes, resolves the account's owned server, and
    persists everything to settings. The frontend only ever polls this
    app's own `/api/plex/status` — see api/plex.py."""

    # How long a box's own Plex gets to claim itself and show up on the
    # account: the updater's next run (a minute at most), then Plex's
    # restart and its first check-in with plex.tv.
    BOX_CLAIM_SECONDS = 300
    BOX_CLAIM_POLL_SECONDS = 5

    def __init__(self, store):
        self.store = store
        self._task: asyncio.Task | None = None
        self._error: str | None = None
        # What a box's sign-in is doing after the PIN: "claiming" its Plex,
        # then "libraries". None otherwise.
        self._stage: str | None = None

    def _client(self) -> PlexClient:
        return persisted_client(self.store)

    async def start(self) -> str:
        client = self._client()
        self._error = None
        self._stage = None
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
                    if box_plex.managed():
                        owned = await self._box_server(client, token)
                        if owned is None:
                            return
                    else:
                        resources = await asyncio.to_thread(client.list_resources, token)
                        owned = [r for r in resources if r["owned"]]
                    if not owned:
                        self._error = "Signed in, but no Plex server was found on this account."
                        return
                    username = await asyncio.to_thread(client.get_account_username, token)
                    await asyncio.to_thread(
                        self.store.update_settings, {"plex_token": token, "plex_username": username}
                    )
                    await asyncio.to_thread(self._keep_linked_servers, owned)
                    if box_plex.managed():
                        await self._box_libraries(owned[0])
                    return
                await asyncio.sleep(PIN_POLL_INTERVAL_SECONDS)
            self._error = "Plex sign-in timed out — try again."
        except Exception as exc:  # fail safe — never leave the frontend polling forever
            self._error = str(exc)

    async def _box_server(self, client: PlexClient, token: str) -> list[dict] | None:
        """On a box: its own Plex, claimed for this account if it isn't
        yet, as the one server to link. None (with the reason in _error)
        when that can't be done."""
        url = box_plex.local_url()
        found = await asyncio.to_thread(box_plex.identity, url) if url else None
        if not found:
            self._error = "Couldn't reach this box's Plex server. Give it a minute and try again."
            return None
        if not found["claimed"]:
            if not channel.updater_running():
                self._error = "This box isn't ready to set up Plex yet. Give it a minute and try again."
                return None
            self._stage = "claiming"
            claim = await asyncio.to_thread(client.claim_token, token)
            await asyncio.to_thread(box_plex.request_claim, claim)
        deadline = time.monotonic() + self.BOX_CLAIM_SECONDS
        while True:
            resources = await asyncio.to_thread(client.list_resources, token)
            mine = next(
                (r for r in resources if r["owned"] and r["machine_identifier"] == found["machine_identifier"]), None
            )
            if mine:
                # Reached directly on the box, rather than by whatever
                # address plex.tv lists, which may need DNS the home
                # router won't give.
                return [{**mine, "url": url}]
            if found["claimed"]:
                self._error = (
                    "This box's Plex server belongs to a different Plex account. "
                    "Sign in with that account, or contact support to reset it."
                )
                return None
            if time.monotonic() > deadline:
                state = box_plex.claim_state()
                detail = f" ({state['message']})" if state.get("state") == "failed" and state.get("message") else ""
                self._error = f"This box's Plex server didn't finish setting up{detail}. Try signing in again."
                return None
            await asyncio.sleep(self.BOX_CLAIM_POLL_SECONDS)

    async def _box_libraries(self, server: dict) -> None:
        """The box's Movies and TV Shows libraries. Missing ones only, so
        signing in again never makes doubles; a failure is logged rather
        than undoing a sign-in that worked."""
        self._stage = "libraries"
        try:
            changes = await asyncio.to_thread(box_plex.sync_libraries, server["url"], server["token"])
            if changes:
                logger.info("Plex libraries on the box: %s", "; ".join(changes))
        except Exception:  # noqa: BLE001
            logger.exception("couldn't create the box's Plex libraries")
        finally:
            self._stage = None

    def _keep_linked_servers(self, owned: list[dict]) -> None:
        """After a sign-in: every server already linked that this account
        still owns gets fresh connection details, and one it no longer
        owns is dropped. The first owned server is linked when that leaves
        none — the setup wizard's starting point, and a re-link that keeps
        the servers it can rather than trading them for whichever one
        plex.tv happened to list first. Each server's machine id is what
        end-user access checks compare against — see access_by_server."""
        by_machine = {r["machine_identifier"]: r for r in owned}
        keep = [s.machine_id for s in self.store.list_plex_servers() if s.machine_id in by_machine]
        if not keep:
            # None of them left: the first owned server takes their place,
            # and their history with it.
            first = owned[0]
            self.store.replace_plex_servers(first["machine_identifier"], first["name"], first["url"], first["token"])
            return
        # Saved before the rest are removed, so there is never a moment
        # with no server linked — which would read as setup not done.
        for machine_id in keep:
            r = by_machine[machine_id]
            self.store.save_plex_server(r["machine_identifier"], r["name"], r["url"], r["token"])
        self.store.remove_plex_servers(keep=tuple(keep))

    def status(self) -> dict:
        settings = self.store.get_settings()
        linked = bool(settings.get("plex_token"))
        primary = self.store.primary_plex_server() if linked else None
        return {
            "linked": linked,
            "username": settings.get("plex_username") if linked else None,
            "server_name": primary.name if primary else None,
            "pending": bool(self._task and not self._task.done()),
            "error": self._error,
            "stage": self._stage,
        }

    def unlink(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._error = None
        self.store.update_settings({"plex_token": None, "plex_username": None})
        self.store.remove_plex_servers()
        # Unlinking removes every server anyone's access was ever
        # checked against — every session (including the admin's own)
        # must re-authenticate rather than keep working against a server
        # that's no longer configured at all.
        self.store.delete_non_admin_sessions()


@dataclass
class _LoginAttempt:
    """One browser's in-flight PIN sign-in. Separate objects per attempt,
    not fields on LoginSession, because the result of an attempt is what
    mints a session — see LoginSession's docstring."""

    task: asyncio.Task | None = None
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)


class LoginSession:
    """The end-user equivalent of PlexLinker's PIN sign-in flow (frontend
    migration Part C3) — same plex.tv PIN mechanism, different purpose:
    PlexLinker links *this app* to a Plex server (persisted, survives
    restarts); LoginSession authenticates *one person* against the
    already-linked servers (a signed-in app session, not app-wide state).
    Deliberately in-memory only, unlike PlexLinker — an interrupted login
    attempt has nothing worth surviving a restart for, the user just
    starts over.

    Keyed by attempt, not global. This used to hold a single
    `_result` for the whole process, which meant `/api/auth/login/status`
    — unauthenticated by design, since there is nobody to authenticate
    as until it succeeds — handed a session cookie for the resolved
    account to *whoever* polled it next, and kept doing so on every
    later poll because the result was never cleared. An attempt id is
    now minted per `start()` and returned to exactly one browser (as an
    HttpOnly cookie), so only that browser can ask after its own
    sign-in, and `finish()` makes the answer single-use."""

    # An unguessable id is the entire access control here, so it wants to
    # be as wide as the session id it can be traded for.
    ATTEMPT_ID_BYTES = 32
    # An attempt is worthless once its PIN has expired; the grace is just
    # so a poll racing the deadline still gets the real "timed out"
    # error rather than a blank "no such attempt".
    ATTEMPT_TTL_SECONDS = PIN_TIMEOUT_SECONDS + 60
    # Abandoned attempts (opened the tab, never finished) each hold a
    # task polling plex.tv until their PIN expires, so the count is
    # bounded rather than left to the route's rate limit alone. Far above
    # anything a household generates at once; the oldest goes first.
    MAX_LIVE_ATTEMPTS = 25

    def __init__(self, store):
        self.store = store
        self._attempts: dict[str, _LoginAttempt] = {}

    def _client(self) -> PlexClient:
        return persisted_client(self.store)

    def _discard(self, attempt_id: str) -> None:
        attempt = self._attempts.pop(attempt_id, None)
        if attempt and attempt.task and not attempt.task.done():
            attempt.task.cancel()

    def _evict(self) -> None:
        cutoff = time.monotonic() - self.ATTEMPT_TTL_SECONDS
        for attempt_id in [k for k, a in self._attempts.items() if a.created_at < cutoff]:
            self._discard(attempt_id)
        # Oldest-first, so a flood of fresh attempts can't push out the
        # one the person in front of you is part-way through.
        while len(self._attempts) >= self.MAX_LIVE_ATTEMPTS:
            oldest = min(self._attempts, key=lambda k: self._attempts[k].created_at)
            self._discard(oldest)

    async def start(self, forward_url: str | None = None) -> tuple[str, str]:
        """(attempt_id, auth_url). The caller is responsible for handing
        the attempt id back to precisely one browser and no further, and
        for vouching for `forward_url` — plex.tv will send the browser
        wherever it says once the PIN is authorised."""
        self._evict()
        client = self._client()
        pin = await asyncio.to_thread(client.create_pin)
        attempt_id = secrets.token_urlsafe(self.ATTEMPT_ID_BYTES)
        attempt = _LoginAttempt()
        self._attempts[attempt_id] = attempt
        attempt.task = asyncio.create_task(self._poll(client, pin["id"], attempt))
        return attempt_id, client.auth_url(pin["code"], forward_url)

    async def _poll(self, client: PlexClient, pin_id: int, attempt: _LoginAttempt) -> None:
        deadline = time.monotonic() + PIN_TIMEOUT_SECONDS
        try:
            while time.monotonic() < deadline:
                token = await asyncio.to_thread(client.check_pin, pin_id)
                if token:
                    servers = self.store.list_plex_servers()
                    if not servers:
                        attempt.error = "This server hasn't linked a Plex account yet."
                        return
                    reachable = await asyncio.to_thread(client.access_by_server, token)
                    # In priority order, so the first is where they land:
                    # someone shared on more than one server gets the one
                    # ranked highest.
                    granted = [(s, reachable[s.machine_id]) for s in servers if s.machine_id in reachable]
                    if not granted:
                        attempt.error = "Your Plex account doesn't have access to this server."
                        return
                    identity = await asyncio.to_thread(client.get_account_identity, token)
                    if not identity or not identity.get("id"):
                        attempt.error = "Couldn't verify the signed-in Plex account."
                        return
                    attempt.result = {
                        "plex_user_id": str(identity["id"]),
                        "username": identity.get("username"),
                        # One account owns every server here, so owning
                        # any of them is owning all of them.
                        "is_admin": any(access["owned"] for _, access in granted),
                        "thumb": identity.get("thumb"),
                        "server_id": granted[0][0].id,
                        # Stored against the user, never sent to the browser
                        # — see db.get_user_server_token.
                        "server_tokens": {server.id: access.get("token") for server, access in granted},
                    }
                    return
                await asyncio.sleep(PIN_POLL_INTERVAL_SECONDS)
            attempt.error = "Plex sign-in timed out — try again."
        except Exception as exc:  # fail safe — never leave the frontend polling forever
            attempt.error = str(exc)

    def status(self, attempt_id: str | None) -> dict:
        """Deliberately indistinguishable between "no attempt id",
        "expired", "already claimed" and "never existed" — a caller
        without a live attempt of their own learns only that there is
        nothing here for them, never that someone else's sign-in is in
        progress or has just landed."""
        attempt = self._attempts.get(attempt_id) if attempt_id else None
        if attempt is None:
            return {"pending": False, "result": None, "error": None}
        return {
            "pending": bool(attempt.task and not attempt.task.done()),
            "result": attempt.result,
            "error": attempt.error,
        }

    def finish(self, attempt_id: str) -> None:
        """Spend the attempt. Called once its result has been traded for
        a session, so one finished sign-in is one session and a replayed
        poll gets nothing."""
        self._discard(attempt_id)
