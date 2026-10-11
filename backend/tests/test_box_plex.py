"""A box's own Plex (app/box_plex.py): claimed for the buyer as they sign in,
linked, and given its libraries — and the NAS updater's half of the claim
(truenas/obsidian-updater.py)."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest

from app import box_plex, config, updates
from app.db import RequestStore
from app.plex import PlexClient, PlexLinker

SCRIPT = Path(__file__).resolve().parents[2] / "truenas" / "obsidian-updater.py"
spec = importlib.util.spec_from_file_location("obsidian_updater_plex", SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

BOX_URL = "http://172.18.0.1:32400"
MACHINE = "box-machine-id"


def box_resource(owned=True):
    return {"name": "obsidian", "url": "https://192-168-0-50.abc.plex.direct:32400", "token": "server-token",
            "owned": owned, "machine_identifier": MACHINE}


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A box mid-setup: its Plex reachable and unclaimed, the updater
    running, the buyer about to approve the sign-in."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(box_plex, "PLEX_LOCAL_URL", BOX_URL)
    updates.write_json(updates.heartbeat_path(), {"at": updates.now_iso(), "script": 4})
    state = {"claimed": False, "resources": [], "libraries": []}
    monkeypatch.setattr(box_plex, "identity", lambda url: {"machine_identifier": MACHINE, "claimed": state["claimed"]})
    monkeypatch.setattr(box_plex, "sync_libraries", lambda url, token: state["libraries"].append((url, token)) or ["made Movies"])
    monkeypatch.setattr(PlexClient, "create_pin", lambda self: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(PlexClient, "check_pin", lambda self, pin_id: "account-token")
    monkeypatch.setattr(PlexClient, "get_account_username", lambda self, token: "buyer")
    monkeypatch.setattr(PlexClient, "claim_token", lambda self, token: "claim-AbC123xyz_-987")

    def resources(self, token):
        # Plex shows up on the account once the updater has handed it the
        # claim — here, the moment the request is there.
        if box_plex.claim_request_path().exists():
            state["claimed"] = True
            return [box_resource()]
        return state["resources"]

    monkeypatch.setattr(PlexClient, "list_resources", resources)
    monkeypatch.setattr(PlexLinker, "BOX_CLAIM_POLL_SECONDS", 0)
    store = RequestStore(":memory:")
    return store, state


def sign_in(store):
    linker = PlexLinker(store)

    async def run():
        await linker.start()
        await linker._task

    asyncio.run(run())
    return linker


def test_signing_in_claims_the_box_s_plex_links_it_and_makes_its_libraries(box):
    store, state = box
    linker = sign_in(store)

    assert linker.status()["error"] is None
    request = json.loads(box_plex.claim_request_path().read_text())
    assert request["claim_token"] == "claim-AbC123xyz_-987"
    assert (os.stat(box_plex.claim_request_path()).st_mode & 0o777) == 0o600
    server = store.primary_plex_server()
    assert server.machine_id == MACHINE
    assert server.url == BOX_URL  # the box itself, not the plex.direct address plex.tv lists
    assert store.get_settings()["plex_token"] == "account-token"
    assert state["libraries"] == [(BOX_URL, "server-token")]
    assert linker.status()["stage"] is None


def test_a_box_already_claimed_by_this_account_is_just_linked(box):
    store, state = box
    state["claimed"] = True
    state["resources"] = [box_resource()]
    sign_in(store)
    assert not box_plex.claim_request_path().exists()
    assert store.primary_plex_server().machine_id == MACHINE


def test_a_box_claimed_by_someone_else_says_so(box):
    store, state = box
    state["claimed"] = True
    linker = sign_in(store)
    assert "different Plex account" in linker.status()["error"]
    assert store.primary_plex_server() is None
    assert not box_plex.claim_request_path().exists()


def test_without_the_updater_running_it_waits_rather_than_hangs(box):
    store, _ = box
    updates.heartbeat_path().unlink()
    linker = sign_in(store)
    assert "isn't ready" in linker.status()["error"]
    assert store.primary_plex_server() is None


def test_an_unreachable_plex_is_said_plainly(box, monkeypatch):
    store, _ = box
    monkeypatch.setattr(box_plex, "identity", lambda url: None)
    linker = sign_in(store)
    assert "Couldn't reach" in linker.status()["error"]


def test_a_claim_that_never_lands_gives_up_with_the_updater_s_reason(box, monkeypatch):
    store, _ = box
    monkeypatch.setattr(PlexClient, "list_resources", lambda self, token: [])
    monkeypatch.setattr(PlexLinker, "BOX_CLAIM_SECONDS", 0)
    updates.write_json(box_plex.claim_state_path(), {"state": "failed", "message": "TrueNAS said no"})
    linker = sign_in(store)
    assert "didn't finish setting up (TrueNAS said no)" in linker.status()["error"]


# -- the pieces --


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self.ok, self._body = status, status < 400, body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class FakePlexHTTP:
    def __init__(self, sections):
        self.sections, self.created, self.edited = sections, [], []

    def get(self, url, headers, timeout):
        return FakeResponse(body={"MediaContainer": {"Directory": self.sections}})

    def post(self, url, headers, params, timeout):
        self.created.append(params)
        return FakeResponse()

    def put(self, url, headers, params, timeout):
        self.edited.append((url.rsplit("/", 1)[1], params))
        return FakeResponse()


FOLDERS = {"movie": ["/media/drive1/Movies", "/media/drive2/Movies"], "tv": ["/media/drive1/TV Shows", "/media/drive2/TV Shows"]}


def locations(params):
    return [value for key, value in params if key == "location"]


def test_libraries_are_made_listing_every_drive():
    http = FakePlexHTTP([])
    changes = box_plex.sync_libraries(BOX_URL, "tok", FOLDERS, session=http)
    assert changes == ["made Movies", "made TV Shows"]
    movies, tv = http.created
    assert dict(movies)["agent"] == "tv.plex.agents.movie" and dict(movies)["scanner"] == "Plex Movie"
    assert locations(movies) == ["/media/drive1/Movies", "/media/drive2/Movies"]
    assert dict(tv)["type"] == "show" and locations(tv) == ["/media/drive1/TV Shows", "/media/drive2/TV Shows"]


def test_a_new_drive_s_folder_is_added_and_nothing_is_taken_away():
    http = FakePlexHTTP([
        {"key": "1", "type": "movie", "title": "Movies", "agent": "tv.plex.agents.movie", "language": "en-US",
         "Location": [{"path": "/media/drive1/Movies"}, {"path": "/mnt/old-nas/films"}]},
        {"key": "2", "type": "show", "title": "TV Shows", "agent": "tv.plex.agents.series",
         "Location": [{"path": "/media/drive1/TV Shows"}, {"path": "/media/drive2/TV Shows"}]},
    ])
    changes = box_plex.sync_libraries(BOX_URL, "tok", FOLDERS, session=http)
    assert http.created == []
    ((key, params),) = http.edited  # TV Shows already has both
    assert key == "1" and dict(params)["agent"] == "tv.plex.agents.movie"
    assert locations(params) == ["/media/drive1/Movies", "/mnt/old-nas/films", "/media/drive2/Movies"]
    assert changes == ["Movies: added /media/drive2/Movies"]


def test_without_a_drive_there_is_no_library_to_make_yet():
    http = FakePlexHTTP([])
    assert box_plex.sync_libraries(BOX_URL, "tok", {"movie": [], "tv": []}, session=http) == []
    assert http.created == []


def test_plex_hears_about_drives_only_when_they_change(box, tmp_path, monkeypatch):
    from app import worker as worker_module

    store, _ = box
    store.save_plex_server(MACHINE, "obsidian", BOX_URL, "server-token")
    folders = {"movie": ["/media/drive1/Movies"], "tv": ["/media/drive1/TV Shows"]}
    monkeypatch.setattr(box_plex, "wanted_folders", lambda: folders)
    told = []
    monkeypatch.setattr(box_plex, "sync_libraries", lambda url, token, f: told.append((url, token, dict(f))) or [])
    w = worker_module.Worker.__new__(worker_module.Worker)
    w.store = store

    asyncio.run(w._sync_box_libraries())
    asyncio.run(w._sync_box_libraries())
    assert told == [(BOX_URL, "server-token", folders)]  # once, then nothing new to say

    folders = {"movie": ["/media/drive1/Movies", "/media/drive2/Movies"], "tv": ["/media/drive1/TV Shows", "/media/drive2/TV Shows"]}
    asyncio.run(w._sync_box_libraries())
    assert len(told) == 2 and told[1][2] == folders


def test_the_box_s_plex_is_found_at_the_container_s_gateway(monkeypatch):
    monkeypatch.setattr(box_plex, "PLEX_LOCAL_URL", "gateway")
    monkeypatch.setattr(box_plex, "default_gateway", lambda: "172.18.0.1")
    assert box_plex.local_url() == "http://172.18.0.1:32400"


def test_reading_the_gateway_from_the_route_table(tmp_path):
    table = tmp_path / "route"
    table.write_text(
        "Iface\tDestination\tGateway \tFlags\n"
        "eth0\t00000000\t010012AC\t0003\n"
        "eth0\t000012AC\t00000000\t0001\n"
    )
    assert box_plex.default_gateway(table) == "172.18.0.1"


# -- the NAS updater's half --


def nas_with_plex(data):
    return {"services": {
        "frontend": {"image": "ghcr.io/thyglizzyglobber/obsidian-frontend:1.0.60", "ports": ["80:80"]},
        "backend": {"image": "ghcr.io/thyglizzyglobber/obsidian-backend:1.0.60", "volumes": [f"{data}:/app/data"]},
        "plex": {"image": "plexinc/pms-docker:1.43.4.10903-e5521bd8c", "network_mode": "host",
                 "environment": {"PLEX_UID": "568", "PLEX_GID": "568", "TZ": "Australia/Sydney"}},
    }}


class NAS:
    def __init__(self, data):
        self.compose, self.updates = nas_with_plex(data), []

    def app_config(self, app):
        return json.loads(json.dumps(self.compose))

    def app_update(self, app, compose):
        self.updates.append(compose)
        self.compose = compose


@pytest.fixture
def nas(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "updater").mkdir(parents=True)
    the_nas = NAS(data)
    the_nas.data = data
    monkeypatch.setattr(host, "_get", lambda url, timeout=5: (200, b'{"version": "1.0.60"}'))
    return the_nas


def run(nas):
    host.Updater(nas, "obsidian", verify_seconds=0, sleep=lambda _s: None).run()


def test_the_updater_hands_plex_the_claim_and_deletes_the_request(nas):
    (nas.data / "updater" / "plex-claim-request.json").write_text(json.dumps({"claim_token": "claim-AbC123xyz_-987"}))
    run(nas)
    env = nas.compose["services"]["plex"]["environment"]
    assert env == {"PLEX_UID": "568", "PLEX_GID": "568", "TZ": "Australia/Sydney", "PLEX_CLAIM": "claim-AbC123xyz_-987"}
    assert nas.compose["services"]["plex"]["network_mode"] == "host"
    assert not (nas.data / "updater" / "plex-claim-request.json").exists()
    assert json.loads((nas.data / "updater" / "plex-claim-state.json").read_text())["state"] == "applied"


@pytest.mark.parametrize("token", ["not-a-claim", "claim-x", "claim-abc\nPLEX_UID=0", None, 7])
def test_anything_but_a_claim_token_is_refused(nas, token):
    (nas.data / "updater" / "plex-claim-request.json").write_text(json.dumps({"claim_token": token}))
    run(nas)
    assert nas.updates == []
    assert not (nas.data / "updater" / "plex-claim-request.json").exists()
    assert json.loads((nas.data / "updater" / "plex-claim-state.json").read_text())["state"] == "failed"
