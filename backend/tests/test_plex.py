import asyncio
import itertools

import pytest

from app import plex
from app.db import RequestStore
from app.plex import LibraryIndex, LoginSession, PlexClient, PlexError, PlexLinker, has_in_library, plex_library_lookup


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data or {}

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, post_response=None, get_responses=None):
        self._post_response = post_response
        self._get_responses = list(get_responses or [])
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []

    def post(self, url, headers=None, data=None, timeout=None):
        self.post_calls.append((url, data))
        return self._post_response

    def get(self, url, headers=None, params=None, timeout=None):
        self.get_calls.append((url, params))
        return self._get_responses.pop(0)


# ---------------------------------------------------------------------------
# PlexClient — pure API wrapper
# ---------------------------------------------------------------------------


def test_create_pin_returns_id_and_code():
    session = FakeSession(post_response=FakeResponse(json_data={"id": 42, "code": "ABCD"}))
    client = PlexClient("client-1", session=session)

    pin = client.create_pin()

    assert pin == {"id": 42, "code": "ABCD"}


def test_create_pin_raises_on_error_response():
    session = FakeSession(post_response=FakeResponse(status_code=500))
    client = PlexClient("client-1", session=session)

    with pytest.raises(PlexError):
        client.create_pin()


def test_auth_url_includes_client_id_and_code():
    client = PlexClient("client-1", session=FakeSession())
    url = client.auth_url("ABCD")

    assert "clientID=client-1" in url
    assert "code=ABCD" in url
    assert url.startswith("https://app.plex.tv/auth#?")


def test_auth_url_forwards_back_to_the_app_when_given_somewhere_to_go():
    client = PlexClient("client-1", session=FakeSession())

    url = client.auth_url("ABCD", "https://obsidian.example/")

    assert "forwardUrl=https%3A%2F%2Fobsidian.example%2F" in url


def test_auth_url_omits_forward_url_when_there_isnt_one():
    client = PlexClient("client-1", session=FakeSession())

    assert "forwardUrl" not in client.auth_url("ABCD")


def test_check_pin_returns_token_once_present():
    session = FakeSession(get_responses=[FakeResponse(json_data={"authToken": "tok-123"})])
    client = PlexClient("client-1", session=session)

    assert client.check_pin(42) == "tok-123"


def test_check_pin_returns_none_while_still_pending():
    session = FakeSession(get_responses=[FakeResponse(json_data={"authToken": None})])
    client = PlexClient("client-1", session=session)

    assert client.check_pin(42) is None


def test_list_resources_includes_owned_and_shared_servers():
    resources = [
        {
            "provides": "server",
            "owned": True,
            "name": "Living Room NAS",
            "clientIdentifier": "machine-owned",
            "accessToken": "owned-token",
            "connections": [{"uri": "http://owned", "local": True, "relay": False}],
        },
        {
            "provides": "server",
            "owned": False,
            "name": "Friend's Server",
            "clientIdentifier": "machine-shared",
            "accessToken": "shared-token",
            "connections": [{"uri": "http://shared", "local": False, "relay": False}],
        },
        {"provides": "player", "owned": True, "connections": [{"uri": "x", "local": True, "relay": False}]},
    ]
    session = FakeSession(get_responses=[FakeResponse(json_data=resources)])
    client = PlexClient("client-1", session=session)

    result = client.list_resources("account-token")

    assert result == [
        {
            "name": "Living Room NAS",
            "url": "http://owned",
            "token": "owned-token",
            "owned": True,
            "machine_identifier": "machine-owned",
        },
        {
            "name": "Friend's Server",
            "url": "http://shared",
            "token": "shared-token",
            "owned": False,
            "machine_identifier": "machine-shared",
        },
    ]


def test_list_resources_prefers_a_local_non_relay_connection():
    resources = [
        {
            "provides": "server",
            "owned": True,
            "name": "Living Room NAS",
            "clientIdentifier": "machine-owned",
            "accessToken": "server-token",
            "connections": [
                {"uri": "https://relay.example", "local": False, "relay": True},
                {"uri": "http://192.168.0.133:32400", "local": True, "relay": False},
            ],
        }
    ]
    session = FakeSession(get_responses=[FakeResponse(json_data=resources)])
    client = PlexClient("client-1", session=session)

    [server] = client.list_resources("account-token")

    assert server["url"] == "http://192.168.0.133:32400"


def test_check_server_access_matches_by_machine_identifier():
    resources = [
        {
            "provides": "server",
            "owned": False,
            "name": "Family Server",
            "clientIdentifier": "our-machine-id",
            "accessToken": "tok",
            "connections": [{"uri": "http://server", "local": True, "relay": False}],
        }
    ]
    session = FakeSession(get_responses=[FakeResponse(json_data=resources)])
    client = PlexClient("client-1", session=session)

    # The access token comes back too: it is this account's own token for
    # that server, and it is what lets a per-user Plex read be made as them
    # rather than as the admin.
    assert client.check_server_access("account-token", "our-machine-id") == {"owned": False, "token": "tok"}


def test_check_server_access_returns_none_when_server_not_in_list():
    session = FakeSession(get_responses=[FakeResponse(json_data=[])])
    client = PlexClient("client-1", session=session)

    assert client.check_server_access("account-token", "our-machine-id") is None


def test_get_account_identity_returns_id_and_username():
    session = FakeSession(get_responses=[FakeResponse(json_data={"id": 42, "username": "bejay"})])
    client = PlexClient("client-1", session=session)

    assert client.get_account_identity("account-token") == {"id": 42, "username": "bejay", "thumb": None}


def test_get_account_identity_falls_back_to_title_when_no_username():
    session = FakeSession(get_responses=[FakeResponse(json_data={"id": 42, "title": "Bejay"})])
    client = PlexClient("client-1", session=session)

    assert client.get_account_identity("account-token") == {"id": 42, "username": "Bejay", "thumb": None}


def test_get_account_identity_returns_none_on_error_response():
    session = FakeSession(get_responses=[FakeResponse(status_code=401)])
    client = PlexClient("client-1", session=session)

    assert client.get_account_identity("account-token") is None


def test_has_movie_matches_normalized_title_and_year():
    metadata = {"MediaContainer": {"Metadata": [{"title": "Dune: Part Two", "year": 2024}]}}
    session = FakeSession(get_responses=[FakeResponse(json_data=metadata)])
    client = PlexClient("client-1", session=session)

    assert client.has_movie("http://server", "tok", "Dune Part Two", 2024) is True


def test_has_movie_false_when_year_mismatch_exceeds_tolerance():
    metadata = {"MediaContainer": {"Metadata": [{"title": "Dune: Part Two", "year": 2020}]}}
    session = FakeSession(get_responses=[FakeResponse(json_data=metadata)])
    client = PlexClient("client-1", session=session)

    assert client.has_movie("http://server", "tok", "Dune Part Two", 2024) is False


def test_has_movie_false_when_no_results():
    metadata = {"MediaContainer": {"Metadata": []}}
    session = FakeSession(get_responses=[FakeResponse(json_data=metadata)])
    client = PlexClient("client-1", session=session)

    assert client.has_movie("http://server", "tok", "Some Movie", None) is False


def test_has_movie_goes_by_tmdb_id_when_plex_has_one():
    metadata = {"MediaContainer": {"Metadata": [{"title": "The Runner", "year": 2026, "Guid": [{"id": "tmdb://1386315"}]}]}}
    session = FakeSession(get_responses=[FakeResponse(json_data=metadata)] * 2)
    client = PlexClient("client-1", session=session)

    assert client.has_movie("http://server", "tok", "Runner", 2026, tmdb_id=42) is False
    assert client.has_movie("http://server", "tok", "The Runner", 2026, tmdb_id=1386315) is True


def test_has_in_library_with_an_id_reads_the_whole_library(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings({"plex_client_id": "client-1", "plex_server_url": "http://server", "plex_server_token": "tok"})
    monkeypatch.setattr(
        PlexClient,
        "library_index",
        lambda self, url, token, media_type: _index({"title": "The Fantastic Four: First Steps", "year": 2025, "tmdb_id": 617126}),
    )

    assert has_in_library(store, "The Fantastic 4: First Steps", 2025, 617126) is True
    assert has_in_library(store, "The Fantastic 4: First Steps", 2025, 1) is False


def test_has_movie_matches_a_plex_title_carrying_a_franchise_prefix():
    """The real bug this covers: Plex's own scraped title was "Star Wars:
    The Mandalorian and Grogu" while TMDB's plain title is "The
    Mandalorian and Grogu" — an exact-string match would miss this
    entirely."""
    metadata = {"MediaContainer": {"Metadata": [{"title": "Star Wars: The Mandalorian and Grogu", "year": 2026}]}}
    session = FakeSession(get_responses=[FakeResponse(json_data=metadata)])
    client = PlexClient("client-1", session=session)

    assert client.has_movie("http://server", "tok", "The Mandalorian and Grogu", 2026) is True


# ---------------------------------------------------------------------------
# library_index / plex_library_lookup — Stage 14's batched, cached "On
# Plex" badge lookup (one bulk fetch per grid render instead of N).
# ---------------------------------------------------------------------------


def test_library_index_reads_title_year_tmdb_id_and_rating_key():
    metadata = {
        "MediaContainer": {
            "Metadata": [
                {"title": "Dune: Part Two", "year": 2024, "ratingKey": 7, "Guid": [{"id": "imdb://tt1"}, {"id": "tmdb://693134"}]},
                {"title": "Lanterns", "year": 2026},
                {"title": "", "year": 2020},  # no title: skipped
            ]
        }
    }
    session = FakeSession(get_responses=[FakeResponse(json_data=metadata)])
    client = PlexClient("client-1", session=session)

    index = client.library_index("http://server", "tok", "movie")

    assert index.items == [
        {"title": "Dune: Part Two", "year": 2024, "tmdb_id": 693134, "rating_key": "7"},
        {"title": "Lanterns", "year": 2026, "tmdb_id": None, "rating_key": None},
    ]
    assert session.get_calls[0][1] == {"type": 1, "includeGuids": 1}


def _index(*items):
    return LibraryIndex([{"tmdb_id": None, "rating_key": None, **i} for i in items])


def test_library_index_matches_by_tmdb_id_before_title():
    """Live 2026-09-18: "Runner" showed as on Plex because "The Runner"
    was, and TMDB's "The Fantastic 4: First Steps" never matched Plex's
    "The Fantastic Four: First Steps"."""
    index = _index(
        {"title": "The Runner", "year": 2026, "tmdb_id": 1386315},
        {"title": "The Fantastic Four: First Steps", "year": 2025, "tmdb_id": 617126},
        {"title": "Old Agent Movie", "year": 2001},
    )

    assert index.find("Runner", 2026, 999) is None  # different id, title collision ignored
    assert index.find("The Runner", 2026, 1386315) is not None
    assert index.find("The Fantastic 4: First Steps", 2025, 617126)["title"] == "The Fantastic Four: First Steps"
    assert index.find("Old Agent Movie", 2001, 5) is not None  # untagged item: title decides
    assert index.find("Runner", 2026) is not None  # no id to go on: title rules as before


def test_library_index_show_type_uses_type_2():
    session = FakeSession(get_responses=[FakeResponse(json_data={"MediaContainer": {"Metadata": []}})])
    client = PlexClient("client-1", session=session)

    client.library_index("http://server", "tok", "show")

    assert session.get_calls[0][1] == {"type": 2, "includeGuids": 1}


def test_library_index_raises_on_error_response():
    session = FakeSession(get_responses=[FakeResponse(status_code=500)])
    client = PlexClient("client-1", session=session)

    with pytest.raises(PlexError):
        client.library_index("http://server", "tok", "movie")


def test_plex_library_lookup_returns_none_when_not_linked():
    store = RequestStore(":memory:")
    assert plex_library_lookup(store, "movie") is None


def test_plex_library_lookup_matches_title_and_year(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings(
        {"plex_client_id": "client-1", "plex_server_url": "http://server-a", "plex_server_token": "tok"}
    )
    monkeypatch.setattr(
        PlexClient, "library_index", lambda self, url, token, media_type: _index({"title": "Dune: Part Two", "year": 2024})
    )

    matcher = plex_library_lookup(store, "movie")

    assert matcher("Dune: Part Two", 2024) is True
    assert matcher("Dune: Part Two", 2030) is False  # outside year tolerance
    assert matcher("Some Other Movie", None) is False


def test_plex_library_lookup_year_tolerance_and_no_year_given(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings(
        {"plex_client_id": "client-1", "plex_server_url": "http://server-b", "plex_server_token": "tok"}
    )
    monkeypatch.setattr(PlexClient, "library_index", lambda self, url, token, media_type: _index({"title": "Lanterns", "year": 2026}))

    matcher = plex_library_lookup(store, "show")

    assert matcher("Lanterns", 2027) is True  # within YEAR_TOLERANCE
    assert matcher("Lanterns", None) is True  # title matched, no year to check further


def test_plex_library_lookup_matches_a_franchise_prefixed_title(monkeypatch):
    """Same real bug as test_has_movie_matches_a_plex_title_carrying_a_
    franchise_prefix, through the batched/cached lookup path — the index
    is keyed by Plex's own (prefixed) normalized title, and a plain TMDB
    title has to still resolve against it via the fuzzy fallback."""
    store = RequestStore(":memory:")
    store.update_settings(
        {"plex_client_id": "client-1", "plex_server_url": "http://server-e", "plex_server_token": "tok"}
    )
    monkeypatch.setattr(
        PlexClient,
        "library_index",
        lambda self, url, token, media_type: _index({"title": "Star Wars: The Mandalorian and Grogu", "year": 2026}),
    )

    matcher = plex_library_lookup(store, "movie")

    assert matcher("The Mandalorian and Grogu", 2026) is True
    assert matcher("The Mandalorian and Grogu", 2010) is False  # outside year tolerance
    assert matcher("Grogu", None) is True  # single-word match allowed, at the user's explicit request


def test_plex_library_lookup_fails_safe_on_plex_error(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings(
        {"plex_client_id": "client-1", "plex_server_url": "http://server-c", "plex_server_token": "tok"}
    )

    def raise_error(self, url, token, media_type):
        raise PlexError("boom")

    monkeypatch.setattr(PlexClient, "library_index", raise_error)

    matcher = plex_library_lookup(store, "movie")

    assert matcher("Anything", 2024) is False


def test_plex_library_lookup_caches_within_ttl(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings(
        {"plex_client_id": "client-1", "plex_server_url": "http://server-d", "plex_server_token": "tok"}
    )
    calls = []
    monkeypatch.setattr(
        PlexClient, "library_index", lambda self, url, token, media_type: calls.append(1) or _index()
    )

    plex_library_lookup(store, "movie")
    plex_library_lookup(store, "movie")

    assert len(calls) == 1  # second call hit the cache, no second Plex fetch


# ---------------------------------------------------------------------------
# has_in_library — the worker's completion-check helper
# ---------------------------------------------------------------------------


def test_has_in_library_returns_none_when_plex_not_linked():
    store = RequestStore(":memory:")
    assert has_in_library(store, "Some Movie", 2024) is None


def test_has_in_library_delegates_to_plex_client_when_linked(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings(
        {"plex_client_id": "client-1", "plex_server_url": "http://server", "plex_server_token": "tok"}
    )
    calls = []
    monkeypatch.setattr(
        PlexClient, "has_movie", lambda self, server_url, server_token, title, year, tmdb_id=None: calls.append((title, year)) or True
    )

    assert has_in_library(store, "Some Movie", 2024) is True
    assert calls == [("Some Movie", 2024)]


# ---------------------------------------------------------------------------
# PlexLinker — the background PIN sign-in flow
# ---------------------------------------------------------------------------


def test_linker_start_returns_auth_url_and_persists_client_id(monkeypatch):
    store = RequestStore(":memory:")
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: None)  # never resolves in this test
    linker = PlexLinker(store)

    async def run():
        url = await linker.start()
        linker._task.cancel()
        return url

    url = asyncio.run(run())

    assert "code=ABCD" in url
    assert store.get_settings().get("plex_client_id")


def test_linker_poll_persists_token_username_and_server_once_signed_in(monkeypatch):
    store = RequestStore(":memory:")
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "the-token")
    monkeypatch.setattr(
        PlexClient,
        "list_resources",
        lambda self, token: [
            {
                "name": "Living Room NAS",
                "url": "http://server",
                "token": "server-token",
                "owned": True,
                "machine_identifier": "machine-abc",
            }
        ],
    )
    monkeypatch.setattr(PlexClient, "get_account_username", lambda self, token: "bejay")
    linker = PlexLinker(store)

    async def run():
        await linker.start()
        for _ in range(50):
            if store.get_settings().get("plex_token"):
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    settings = store.get_settings()
    assert settings["plex_token"] == "the-token"
    assert settings["plex_username"] == "bejay"
    assert settings["plex_server_url"] == "http://server"
    assert settings["plex_server_token"] == "server-token"
    assert settings["plex_server_machine_id"] == "machine-abc"

    status = linker.status()
    assert status["linked"] is True
    assert status["username"] == "bejay"
    assert status["server_name"] == "Living Room NAS"


def test_linker_poll_records_error_when_signed_in_but_no_server_found(monkeypatch):
    store = RequestStore(":memory:")
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "the-token")
    monkeypatch.setattr(PlexClient, "list_resources", lambda self, token: [])
    linker = PlexLinker(store)

    async def run():
        await linker.start()
        for _ in range(50):
            if linker.status()["error"]:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    assert not store.get_settings().get("plex_token")
    assert "no Plex server" in linker.status()["error"]


def test_linker_unlink_clears_settings_and_cancels_pending_poll(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings({"plex_token": "tok", "plex_username": "bejay", "plex_server_url": "http://server"})
    linker = PlexLinker(store)

    linker.unlink()

    settings = store.get_settings()
    assert settings["plex_token"] is None
    assert settings["plex_username"] is None
    assert linker.status()["linked"] is False


def test_linker_unlink_clears_machine_id_and_revokes_non_admin_sessions(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings(
        {"plex_token": "tok", "plex_server_url": "http://server", "plex_server_machine_id": "machine-abc"}
    )
    user = store.upsert_user("friend-1", "friend", False)
    store.create_session("sess-1", user.plex_user_id, user.username, False, "2999-01-01T00:00:00+00:00")
    linker = PlexLinker(store)

    linker.unlink()

    assert store.get_settings()["plex_server_machine_id"] is None
    assert store.get_session("sess-1") is None


# ---------------------------------------------------------------------------
# LoginSession — the end-user equivalent of PlexLinker's PIN sign-in flow
# ---------------------------------------------------------------------------


def test_login_session_persists_result_once_signed_in_with_server_access(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings({"plex_server_machine_id": "our-machine"})
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "user-token")
    monkeypatch.setattr(
        PlexClient, "check_server_access", lambda self, token, machine_id: {"owned": False, "token": "their-token"}
    )
    monkeypatch.setattr(
        PlexClient, "get_account_identity", lambda self, token: {"id": 99, "username": "friend"}
    )
    login = LoginSession(store)
    attempt: list[str] = []

    async def run():
        attempt_id, _ = await login.start()
        attempt.append(attempt_id)
        for _ in range(50):
            if login.status(attempt_id)["result"]:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    status = login.status(attempt[0])
    assert status["result"] == {
        "plex_user_id": "99",
        "username": "friend",
        "is_admin": False,
        "thumb": None,
        # Carried through to be stored against the user; never sent onward
        # to the browser.
        "server_token": "their-token",
    }
    assert status["error"] is None


def test_login_session_records_error_when_account_has_no_server_access(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings({"plex_server_machine_id": "our-machine"})
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "user-token")
    monkeypatch.setattr(PlexClient, "check_server_access", lambda self, token, machine_id: None)
    login = LoginSession(store)
    attempt: list[str] = []

    async def run():
        attempt_id, _ = await login.start()
        attempt.append(attempt_id)
        for _ in range(50):
            if login.status(attempt_id)["error"]:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    assert login.status(attempt[0])["result"] is None
    assert "doesn't have access" in login.status(attempt[0])["error"]


def test_login_session_records_error_when_no_server_linked_yet(monkeypatch):
    store = RequestStore(":memory:")  # no plex_server_machine_id at all
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "user-token")
    login = LoginSession(store)
    attempt: list[str] = []

    async def run():
        attempt_id, _ = await login.start()
        attempt.append(attempt_id)
        for _ in range(50):
            if login.status(attempt_id)["error"]:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    assert "hasn't linked a Plex account" in login.status(attempt[0])["error"]


def _resolving_client(monkeypatch, pins, resolves_pin_id=1):
    """PlexClient patched so create_pin hands out `pins` in order and
    only `resolves_pin_id` ever comes back signed in. Keyed on the pin
    id rather than on call order because the poll tasks start running at
    an await point the test doesn't control — swapping check_pin between
    two `start()` calls races them."""
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: next(pins))
    monkeypatch.setattr(
        PlexClient, "check_pin", lambda self, pin_id: "user-token" if pin_id == resolves_pin_id else None
    )
    monkeypatch.setattr(PlexClient, "check_server_access", lambda self, token, machine_id: {"owned": False})
    monkeypatch.setattr(
        PlexClient, "get_account_identity", lambda self, token: {"id": 99, "username": "friend"}
    )


def test_login_session_status_is_blank_for_an_unknown_attempt():
    login = LoginSession(RequestStore(":memory:"))

    assert login.status(None) == {"pending": False, "result": None, "error": None}
    assert login.status("never-issued") == {"pending": False, "result": None, "error": None}


def test_login_session_keeps_concurrent_attempts_apart(monkeypatch):
    """Two people signing in at once must not see each other's result.
    The single-`_result` version had no way to tell them apart, so
    whoever polled next collected whichever sign-in had just landed."""
    store = RequestStore(":memory:")
    store.update_settings({"plex_server_machine_id": "our-machine"})
    _resolving_client(monkeypatch, iter([{"id": 1, "code": "A"}, {"id": 2, "code": "B"}]), resolves_pin_id=1)
    login = LoginSession(store)
    seen = {}

    async def run():
        signed_in, _ = await login.start()
        bystander, _ = await login.start()
        for _ in range(50):
            if login.status(signed_in)["result"]:
                break
            await asyncio.sleep(0.02)
        seen["signed_in"] = login.status(signed_in)
        seen["bystander"] = login.status(bystander)

    asyncio.run(run())

    assert seen["signed_in"]["result"]["plex_user_id"] == "99"
    assert seen["bystander"]["result"] is None
    assert seen["bystander"]["error"] is None


def test_login_session_finish_spends_the_attempt(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings({"plex_server_machine_id": "our-machine"})
    _resolving_client(monkeypatch, iter([{"id": 1, "code": "A"}]))
    login = LoginSession(store)
    claimed = {}

    async def run():
        attempt_id, _ = await login.start()
        for _ in range(50):
            if login.status(attempt_id)["result"]:
                break
            await asyncio.sleep(0.02)
        claimed["id"] = attempt_id
        claimed["before"] = login.status(attempt_id)["result"]
        login.finish(attempt_id)

    asyncio.run(run())

    assert claimed["before"] is not None
    assert login.status(claimed["id"])["result"] is None
    assert claimed["id"] not in login._attempts


def test_login_session_bounds_how_many_attempts_it_keeps(monkeypatch):
    """Abandoned sign-ins each hold a task polling plex.tv until their
    PIN expires, so they can't be allowed to pile up unboundedly."""
    store = RequestStore(":memory:")
    _resolving_client(monkeypatch, itertools.cycle([{"id": 7, "code": "A"}]), resolves_pin_id=-1)
    login = LoginSession(store)

    async def run():
        for _ in range(LoginSession.MAX_LIVE_ATTEMPTS + 10):
            await login.start()

    asyncio.run(run())

    assert len(login._attempts) <= LoginSession.MAX_LIVE_ATTEMPTS


def test_login_session_evicts_attempts_once_their_pin_has_expired(monkeypatch):
    store = RequestStore(":memory:")
    _resolving_client(monkeypatch, itertools.cycle([{"id": 7, "code": "A"}]), resolves_pin_id=-1)
    login = LoginSession(store)
    stale = {}

    async def run():
        attempt_id, _ = await login.start()
        login._attempts[attempt_id].created_at -= LoginSession.ATTEMPT_TTL_SECONDS + 1
        stale["id"] = attempt_id
        await login.start()  # any new attempt sweeps the expired ones

    asyncio.run(run())

    assert stale["id"] not in login._attempts
    assert login.status(stale["id"]) == {"pending": False, "result": None, "error": None}


def test_login_session_reports_a_live_attempt_as_pending(monkeypatch):
    """The login page treats "not pending, no result, no error" as "the
    backend has no live attempt for me" and stops polling, so a live
    attempt must never look like that — otherwise a perfectly good
    sign-in gets abandoned mid-flight."""
    store = RequestStore(":memory:")
    _resolving_client(monkeypatch, itertools.cycle([{"id": 7, "code": "A"}]), resolves_pin_id=-1)
    login = LoginSession(store)
    seen = {}

    async def run():
        attempt_id, _ = await login.start()
        await asyncio.sleep(0.05)
        seen["status"] = login.status(attempt_id)

    asyncio.run(run())

    assert seen["status"] == {"pending": True, "result": None, "error": None}


# ---------------------------------------------------------------------------
# Which audio track Plex actually uses.
#
# A file's default flag only decides Plex's first choice. Plex records what
# it picked when the item was scanned and keeps it — Refresh Metadata and
# Analyze both leave it alone — so a flag corrected afterwards changes
# nothing. Confirmed live 2026-09-27 on Supernatural s14e01, where Plex
# reported the English track as `default` and the Italian one as
# `selected`, having re-read the file correctly.
# ---------------------------------------------------------------------------


class _FakePlex:
    def __init__(self, items):
        self._items = items
        self.selected = []

    def sections(self, url, token):
        return [{"key": "2", "type": "show", "title": "TV Shows", "locations": []}]

    def section_items(self, url, token, key, item_type):
        return [{"ratingKey": k} for k in self._items]

    def metadata(self, url, token, rating_key):
        return self._items[rating_key]

    def select_audio_stream(self, url, token, part_id, stream_id):
        self.selected.append((part_id, stream_id))


def _episode(title, streams):
    return {
        "title": title,
        "Media": [{"Part": [{"id": f"part-{title}", "Stream": streams}]}],
    }


def _audio(sid, lang, default=False, selected=False):
    return {"id": sid, "streamType": 2, "languageCode": lang, "default": default, "selected": selected}


def _store(tmp_path):
    store = RequestStore(str(tmp_path / "t.db"))
    store.update_settings({"plex_server_url": "http://plex:32400", "plex_server_token": "tok"})
    return store


def test_a_part_plex_left_on_the_wrong_track_is_planned(tmp_path):
    """The live case: Plex read the corrected flag and kept its old
    choice anyway."""
    store = _store(tmp_path)
    fake = _FakePlex({
        "1": _episode("s14e01", [_audio("10", "ita", selected=True), _audio("11", "eng", default=True)]),
    })

    plan = plex.plan_audio_selection(store, fake, "en")

    assert len(plan) == 1
    assert plan[0].stream_id == "11"
    assert plan[0].from_language == "ita"
    store.close()


def test_a_part_already_on_the_right_track_is_left_alone(tmp_path):
    """Changing as little as possible is the point — this runs over a
    whole library."""
    store = _store(tmp_path)
    fake = _FakePlex({
        "1": _episode("ok", [_audio("10", "ita"), _audio("11", "eng", default=True, selected=True)]),
    })

    assert plex.plan_audio_selection(store, fake, "en") == []
    store.close()


def test_a_single_track_part_is_never_touched(tmp_path):
    """Nothing to choose between, and Plex's own `selected` on a lone
    track means nothing."""
    store = _store(tmp_path)
    fake = _FakePlex({"1": _episode("solo", [_audio("10", "ita", selected=True)])})

    assert plex.plan_audio_selection(store, fake, "en") == []
    store.close()


def test_a_part_with_no_track_in_that_language_is_left_alone(tmp_path):
    """A foreign-language film with no English track keeps its own
    audio rather than being pointed at a track that isn't there."""
    store = _store(tmp_path)
    fake = _FakePlex({"1": _episode("foreign", [_audio("10", "jpn", selected=True), _audio("11", "kor")])})

    assert plex.plan_audio_selection(store, fake, "en") == []
    store.close()


def test_applying_sends_the_stream_id_for_that_part(tmp_path):
    store = _store(tmp_path)
    fake = _FakePlex({
        "1": _episode("s14e01", [_audio("10", "ita", selected=True), _audio("11", "eng", default=True)]),
    })
    plan = plex.plan_audio_selection(store, fake, "en")

    assert plex.apply_audio_selection(store, fake, plan[0]) is None
    assert fake.selected == [("part-s14e01", "11")]
    store.close()


def test_an_unlinked_server_says_so_rather_than_failing_obscurely(tmp_path):
    store = RequestStore(str(tmp_path / "t.db"))

    with pytest.raises(plex.PlexError):
        plex.plan_audio_selection(store, _FakePlex({}), "en")
    store.close()


# ---------------------------------------------------------------------------
# Telling Plex after an import, rather than trusting it to have read the
# flag the file asks for.
#
# The Gorge, live: the file had English as its only default track and Plex
# played the Russian dub. Setting the flag decides Plex's first choice and
# nothing after it.
# ---------------------------------------------------------------------------


class _LocatableePlex(_FakePlex):
    def __init__(self, items, found_on_attempt=1):
        super().__init__(items)
        self.found_on_attempt = found_on_attempt
        self.locate_calls = 0

    def locate(self, url, token, media_type, title, year, tmdb_id=None):
        self.locate_calls += 1
        if self.locate_calls < self.found_on_attempt:
            return None
        return {"rating_key": "1", "title": title, "year": year}


def test_a_freshly_imported_title_is_put_on_the_right_track(tmp_path):
    store = _store(tmp_path)
    fake = _LocatableePlex({
        "1": _episode("The Gorge", [
            _audio("10", "rus", selected=True),
            _audio("11", "eng", default=True),
        ]),
    })

    changed = plex.select_audio_after_import(store, fake, "movie", "The Gorge", 2025, 950396, "en", delay=0)

    assert changed == 1
    assert fake.selected == [("part-The Gorge", "11")]
    store.close()


def test_it_waits_for_plex_to_finish_scanning(tmp_path):
    """The refresh only *asks* Plex to look, so the part may not exist
    for a few seconds yet."""
    store = _store(tmp_path)
    fake = _LocatableePlex(
        {"1": _episode("The Gorge", [_audio("10", "rus", selected=True), _audio("11", "eng", default=True)])},
        found_on_attempt=3,
    )

    changed = plex.select_audio_after_import(store, fake, "movie", "The Gorge", 2025, 950396, "en", delay=0)

    assert changed == 1
    assert fake.locate_calls == 3
    store.close()


def test_a_title_plex_never_indexes_gives_up_quietly(tmp_path):
    """A correct file in the right place is still a success; this is the
    belt, not the braces."""
    store = _store(tmp_path)
    fake = _LocatableePlex({"1": _episode("x", [])}, found_on_attempt=99)

    assert plex.select_audio_after_import(store, fake, "movie", "Nope", 2025, 1, "en", attempts=2, delay=0) == 0
    store.close()


def test_nothing_happens_without_a_plex_server(tmp_path):
    """No retries, no sleeping — a household that never linked Plex must
    not pay a minute of waiting per import."""
    store = RequestStore(str(tmp_path / "t.db"))
    fake = _LocatableePlex({})

    assert plex.select_audio_after_import(store, fake, "movie", "x", 2025, 1, "en") == 0
    assert fake.locate_calls == 0
    store.close()


def test_an_import_already_on_the_right_track_changes_nothing(tmp_path):
    """Idempotent, because this runs after every single import."""
    store = _store(tmp_path)
    fake = _LocatableePlex({
        "1": _episode("Fine", [_audio("10", "rus"), _audio("11", "eng", default=True, selected=True)]),
    })

    assert plex.select_audio_after_import(store, fake, "movie", "Fine", 2025, 1, "en", delay=0) == 0
    assert fake.selected == []
    store.close()
