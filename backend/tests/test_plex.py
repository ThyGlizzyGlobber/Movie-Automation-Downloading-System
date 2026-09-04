import asyncio

import pytest

from app.db import RequestStore
from app.plex import PlexClient, PlexError, PlexLinker, has_in_library


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
        "get_owned_server",
        lambda self, token: {"name": "Living Room NAS", "url": "http://server", "token": "server-token"},
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

    status = linker.status()
    assert status["linked"] is True
    assert status["username"] == "bejay"
    assert status["server_name"] == "Living Room NAS"


def test_linker_poll_records_error_when_signed_in_but_no_server_found(monkeypatch):
    store = RequestStore(":memory:")
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "the-token")
    monkeypatch.setattr(PlexClient, "get_owned_server", lambda self, token: None)
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
