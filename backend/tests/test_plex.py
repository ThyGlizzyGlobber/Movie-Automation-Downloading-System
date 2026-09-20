import asyncio
import itertools

import pytest

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
        PlexClient, "check_server_access", lambda self, token, machine_id: {"owned": False}
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
    assert status["result"] == {"plex_user_id": "99", "username": "friend", "is_admin": False, "thumb": None}
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
