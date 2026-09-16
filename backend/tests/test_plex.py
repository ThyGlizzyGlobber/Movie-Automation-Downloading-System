import asyncio

import pytest

from app.db import RequestStore
from app.plex import LoginSession, PlexClient, PlexError, PlexLinker, has_in_library, plex_library_lookup


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


def test_check_pin_returns_token_once_present():
    session = FakeSession(get_responses=[FakeResponse(json_data={"authToken": "tok-123"})])
    client = PlexClient("client-1", session=session)

    assert client.check_pin(42) == "tok-123"


def test_check_pin_returns_none_while_still_pending():
    session = FakeSession(get_responses=[FakeResponse(json_data={"authToken": None})])
    client = PlexClient("client-1", session=session)

    assert client.check_pin(42) is None


def test_get_owned_server_picks_local_non_relay_connection():
    resources = [
        {
            "provides": "server",
            "owned": True,
            "name": "Living Room NAS",
            "accessToken": "server-token",
            "connections": [
                {"uri": "https://relay.example", "local": False, "relay": True},
                {"uri": "http://192.168.0.133:32400", "local": True, "relay": False},
            ],
        }
    ]
    session = FakeSession(get_responses=[FakeResponse(json_data=resources)])
    client = PlexClient("client-1", session=session)

    server = client.get_owned_server("account-token")

    assert server == {"name": "Living Room NAS", "url": "http://192.168.0.133:32400", "token": "server-token"}


def test_get_owned_server_skips_unowned_and_non_server_resources():
    resources = [
        {"provides": "player", "owned": True, "connections": [{"uri": "x", "local": True, "relay": False}]},
        {"provides": "server", "owned": False, "connections": [{"uri": "y", "local": True, "relay": False}]},
    ]
    session = FakeSession(get_responses=[FakeResponse(json_data=resources)])
    client = PlexClient("client-1", session=session)

    assert client.get_owned_server("account-token") is None


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

    assert client.check_server_access("account-token", "our-machine-id") == {"owned": False}


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


def test_library_index_groups_years_by_normalized_title():
    metadata = {
        "MediaContainer": {
            "Metadata": [
                {"title": "Dune: Part Two", "year": 2024},
                {"title": "dune part two", "year": 2025},  # same normalized key, different posting
                {"title": "Lanterns", "year": 2026},
            ]
        }
    }
    session = FakeSession(get_responses=[FakeResponse(json_data=metadata)])
    client = PlexClient("client-1", session=session)

    index = client.library_index("http://server", "tok", "movie")

    assert index == {"dune part two": [2024, 2025], "lanterns": [2026]}
    assert session.get_calls[0][1] == {"type": 1}


def test_library_index_show_type_uses_type_2():
    session = FakeSession(get_responses=[FakeResponse(json_data={"MediaContainer": {"Metadata": []}})])
    client = PlexClient("client-1", session=session)

    client.library_index("http://server", "tok", "show")

    assert session.get_calls[0][1] == {"type": 2}


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
        PlexClient, "library_index", lambda self, url, token, media_type: {"dune part two": [2024]}
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
    monkeypatch.setattr(PlexClient, "library_index", lambda self, url, token, media_type: {"lanterns": [2026]})

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
        lambda self, url, token, media_type: {"star wars the mandalorian and grogu": [2026]},
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
        PlexClient, "library_index", lambda self, url, token, media_type: calls.append(1) or {}
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
        PlexClient, "has_movie", lambda self, server_url, server_token, title, year: calls.append((title, year)) or True
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
        PlexClient, "check_server_access", lambda self, token, machine_id: {"owned": False}
    )
    monkeypatch.setattr(
        PlexClient, "get_account_identity", lambda self, token: {"id": 99, "username": "friend"}
    )
    login = LoginSession(store)

    async def run():
        await login.start()
        for _ in range(50):
            if login.status()["result"]:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    status = login.status()
    assert status["result"] == {"plex_user_id": "99", "username": "friend", "is_admin": False, "thumb": None}
    assert status["error"] is None


def test_login_session_records_error_when_account_has_no_server_access(monkeypatch):
    store = RequestStore(":memory:")
    store.update_settings({"plex_server_machine_id": "our-machine"})
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "user-token")
    monkeypatch.setattr(PlexClient, "check_server_access", lambda self, token, machine_id: None)
    login = LoginSession(store)

    async def run():
        await login.start()
        for _ in range(50):
            if login.status()["error"]:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    assert login.status()["result"] is None
    assert "doesn't have access" in login.status()["error"]


def test_login_session_records_error_when_no_server_linked_yet(monkeypatch):
    store = RequestStore(":memory:")  # no plex_server_machine_id at all
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "user-token")
    login = LoginSession(store)

    async def run():
        await login.start()
        for _ in range(50):
            if login.status()["error"]:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    assert "hasn't linked a Plex account" in login.status()["error"]
