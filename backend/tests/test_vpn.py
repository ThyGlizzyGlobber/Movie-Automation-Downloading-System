"""The box's VPN: checking what a buyer enters (app/box/vpn.py), the routes that
take it (app/api/vpn.py), and the NAS updater applying it to gluetun
(truenas/obsidian-updater.py) — which checks it all again for itself."""

import base64
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.api import deps
from app.box import vpn
from app.core import config
from app.db import RequestStore
from app.updater import channel

SCRIPT = Path(__file__).resolve().parents[2] / "truenas" / "obsidian-updater.py"
spec = importlib.util.spec_from_file_location("obsidian_updater_vpn", SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

KEY = base64.b64encode(bytes(range(32))).decode()
OTHER_KEY = base64.b64encode(bytes(range(32, 64))).decode()


# -- what the app accepts --


def test_the_app_and_the_nas_updater_agree_on_the_providers():
    assert {name: {k: v for k, v in spec.items() if k != "label"} for name, spec in vpn.PROVIDERS.items()} == host.VPN_PROVIDERS


def test_mullvad_wireguard_details_are_tidied():
    cleaned = vpn.clean({"provider": "mullvad", "private_key": f" {KEY} ", "addresses": "10.64.0.2/32 ",
                         "server_countries": "Sweden, Switzerland"})
    assert cleaned == {"provider": "mullvad", "type": "wireguard", "private_key": KEY,
                       "addresses": "10.64.0.2/32", "server_countries": "Sweden,Switzerland"}


def test_openvpn_providers_take_a_username_and_password():
    cleaned = vpn.clean({"provider": "private internet access", "user": "p1234567", "password": "hunter2"})
    assert cleaned["type"] == "openvpn" and cleaned["user"] == "p1234567"


@pytest.mark.parametrize("body, says", [
    ({"provider": "nope"}, "Pick your VPN provider"),
    ({"provider": "mullvad", "type": "openvpn"}, "can't be used with openvpn"),
    ({"provider": "mullvad", "private_key": "short", "addresses": "10.64.0.2/32"}, "44 characters"),
    ({"provider": "mullvad", "private_key": KEY, "addresses": "not an address"}, "10.64.0.2/32"),
    ({"provider": "mullvad", "private_key": KEY}, "WireGuard address"),
    ({"provider": "expressvpn", "user": "me", "password": "a\nVPN_TYPE=openvpn"}, "VPN password"),
    ({"provider": "mullvad", "private_key": KEY, "addresses": "10.64.0.2/32", "server_countries": "Sweden; rm"}, "Countries"),
])
def test_what_won_t_work_is_said_plainly(body, says):
    with pytest.raises(vpn.VPNSettingsError, match=says):
        vpn.clean(body)


def test_a_wireguard_file_becomes_the_custom_provider_s_details():
    fields = vpn.parse_wireguard_file(f"""
[Interface]
PrivateKey = {KEY}
Address = 10.2.0.2/32, fd00::2/128
DNS = 10.2.0.1

[Peer]
PublicKey = {OTHER_KEY}
AllowedIPs = 0.0.0.0/0
Endpoint = 185.159.157.1:51820
""")
    cleaned = vpn.clean(fields)
    assert cleaned == {"provider": "custom", "type": "wireguard", "private_key": KEY, "addresses": "10.2.0.2/32",
                       "public_key": OTHER_KEY, "endpoint_ip": "185.159.157.1", "endpoint_port": "51820"}


def test_something_that_isn_t_a_wireguard_file_is_refused():
    with pytest.raises(vpn.VPNSettingsError, match="WireGuard file"):
        vpn.parse_wireguard_file("hello")


# -- the routes --


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A signed-in admin on a box: gluetun is part of the install and the
    NAS updater is running."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(vpn, "GLUETUN_URL", "http://gluetun:8000")
    monkeypatch.setattr(vpn, "status", lambda: {"reachable": True, "connected": False, "public_ip": None, "country": None})
    channel.write_json(channel.heartbeat_path(), {"at": channel.now_iso(), "script": 3})
    store = RequestStore(str(tmp_path / "test.db"))
    store.save_plex_server("m", "Server", "http://plex.invalid", "t")
    admin = store.upsert_user("admin-id", "admin", True)
    session = store.create_session("s", admin.plex_user_id, admin.username, True,
                                   (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    original = api.app.router.lifespan_context

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store
        app.state.qbt = None
        app.state.worker = None
        yield

    api.app.router.lifespan_context = lifespan
    deps.limiter.reset()
    with TestClient(api.app) as client:
        client.cookies.set(deps.SESSION_COOKIE_NAME, session.id)
        yield client, store
    api.app.router.lifespan_context = original


def test_settings_show_the_providers_and_whether_it_s_connected(box):
    client, _ = box
    body = client.get("/api/admin/vpn").json()
    assert body["managed"] is True
    assert {p["id"] for p in body["providers"]} == set(vpn.PROVIDERS)
    assert body["status"]["connected"] is False
    assert body["applied"]["pending"] is False


def test_saving_leaves_a_private_request_and_returns_no_secret(box):
    client, _ = box
    response = client.put("/api/admin/vpn", json={"provider": "mullvad", "private_key": KEY, "addresses": "10.64.0.2/32"})
    assert response.status_code == 202
    assert KEY not in response.text
    request = json.loads(vpn.request_path().read_text())
    assert request["private_key"] == KEY and request["provider"] == "mullvad"
    assert (os.stat(vpn.request_path()).st_mode & 0o777) == 0o600
    overview = client.get("/api/admin/vpn")
    assert overview.json()["applied"]["pending"] is True
    assert KEY not in overview.text


def test_bad_details_are_refused_with_the_reason(box):
    client, _ = box
    response = client.put("/api/admin/vpn", json={"provider": "mullvad", "private_key": "nope", "addresses": "10.64.0.2/32"})
    assert response.status_code == 422
    assert "44 characters" in response.json()["detail"]
    assert not vpn.request_path().exists()


def test_without_the_updater_running_nothing_is_left_waiting(box):
    client, _ = box
    channel.heartbeat_path().unlink()
    response = client.put("/api/admin/vpn", json={"provider": "mullvad", "private_key": KEY, "addresses": "10.64.0.2/32"})
    assert response.status_code == 409
    assert not vpn.request_path().exists()


def test_an_install_without_its_own_vpn_says_so(box, monkeypatch):
    client, _ = box
    monkeypatch.setattr(vpn, "GLUETUN_URL", None)
    assert client.get("/api/admin/vpn").json() == {"managed": False}
    assert client.put("/api/admin/vpn", json={"provider": "mullvad"}).status_code == 404


def test_the_setup_status_says_whether_the_box_has_a_vpn_and_if_it_s_set(box):
    client, _ = box
    status = client.get("/api/setup/status").json()
    assert (status["vpn_managed"], status["vpn_configured"]) == (True, False)
    channel.write_json(vpn.state_path(), {"state": "applied", "provider": "mullvad", "type": "wireguard"})
    assert client.get("/api/setup/status").json()["vpn_configured"] is True


# -- the NAS updater applying it --


def gluetun_compose(data):
    return {"services": {
        "frontend": {"image": "ghcr.io/thyglizzyglobber/obsidian-frontend:1.0.60", "ports": ["80:80"]},
        "backend": {"image": "ghcr.io/thyglizzyglobber/obsidian-backend:1.0.60", "volumes": [f"{data}:/app/data"]},
        "gluetun": {"image": "qmcgaw/gluetun:v3.41.3", "environment": {
            "FIREWALL_INPUT_PORTS": "8080,8000", "TZ": "Australia/Sydney",
            "HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE": '{"auth":"apikey","apikey":"k"}',
            "OPENVPN_USER": "left over from before", "VPN_TYPE": "openvpn"}},
        "qbittorrent": {"image": "lscr.io/linuxserver/qbittorrent:5.2.4", "network_mode": "service:gluetun"},
    }}


class NAS:
    def __init__(self, data):
        self.compose = gluetun_compose(data)
        self.updates = []

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
    monkeypatch.setattr(host, "_get", lambda url, timeout=5: (200, b'{"version": "1.0.60"}'))
    the_nas.data = data
    return the_nas


def run(nas):
    host.Updater(nas, "obsidian", verify_seconds=0, sleep=lambda _s: None).run()


def test_the_updater_writes_the_details_into_gluetun_and_deletes_the_request(nas):
    (nas.data / "updater" / "vpn-request.json").write_text(json.dumps(
        {"provider": "mullvad", "type": "wireguard", "private_key": KEY, "addresses": "10.64.0.2/32",
         "server_countries": "Sweden"}))
    run(nas)
    env = nas.compose["services"]["gluetun"]["environment"]
    assert env == {
        "FIREWALL_INPUT_PORTS": "8080,8000", "TZ": "Australia/Sydney",
        "HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE": '{"auth":"apikey","apikey":"k"}',
        "VPN_SERVICE_PROVIDER": "mullvad", "VPN_TYPE": "wireguard", "WIREGUARD_PRIVATE_KEY": KEY,
        "WIREGUARD_ADDRESSES": "10.64.0.2/32", "SERVER_COUNTRIES": "Sweden",
    }  # the old OpenVPN user is gone; everything not the VPN's own is kept
    assert nas.compose["services"]["qbittorrent"]["network_mode"] == "service:gluetun"
    assert not (nas.data / "updater" / "vpn-request.json").exists()
    state = json.loads((nas.data / "updater" / "vpn-state.json").read_text())
    assert state["state"] == "applied" and state["provider"] == "mullvad"
    assert KEY not in json.dumps(state)


@pytest.mark.parametrize("request_body", [
    {"provider": "mullvad", "type": "wireguard", "private_key": "x", "addresses": "10.64.0.2/32"},
    {"provider": "mullvad", "type": "wireguard", "private_key": KEY, "addresses": "10.64.0.2/32\nVPN_TYPE=x"},
    {"provider": "evil", "type": "wireguard"},
    {"provider": "custom", "type": "wireguard", "private_key": KEY, "addresses": "10.2.0.2/32",
     "public_key": OTHER_KEY, "endpoint_ip": "vpn.example.com", "endpoint_port": "51820"},
    {"provider": "expressvpn", "type": "openvpn", "user": "me", "password": ["not", "text"]},
])
def test_details_the_updater_won_t_use_change_nothing_and_are_deleted(nas, request_body):
    (nas.data / "updater" / "vpn-request.json").write_text(json.dumps(request_body))
    run(nas)
    assert nas.updates == []
    assert not (nas.data / "updater" / "vpn-request.json").exists()
    assert json.loads((nas.data / "updater" / "vpn-state.json").read_text())["state"] == "failed"


def test_an_app_without_gluetun_can_t_take_vpn_details(nas):
    del nas.compose["services"]["gluetun"]
    (nas.data / "updater" / "vpn-request.json").write_text(json.dumps(
        {"provider": "mullvad", "type": "wireguard", "private_key": KEY, "addresses": "10.64.0.2/32"}))
    run(nas)
    assert nas.updates == []
    assert json.loads((nas.data / "updater" / "vpn-state.json").read_text())["state"] == "failed"
