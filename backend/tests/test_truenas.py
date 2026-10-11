"""app/truenas.py against a fake TrueNAS speaking the real protocol: JSON-RPC
over a TLS WebSocket with a self-signed certificate."""

import socket
import threading

import pytest

from app.truenas import TrueNASClient, TrueNASError, default_gateway
from tests.fake_truenas import API_KEY, API_USER, FakeTrueNAS, self_signed_context

COMPOSE = {
    "services": {
        "backend": {"image": "ghcr.io/thyglizzyglobber/obsidian-backend:1.1.0"},
        "frontend": {"image": "ghcr.io/thyglizzyglobber/obsidian-frontend:1.1.0"},
    }
}


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    return self_signed_context(tmp_path_factory.mktemp("tls"))


@pytest.fixture(scope="module")
def server(tls):
    """One server for the module, as the updater only ever has the one
    TrueNAS. (A server started and stopped per test now and then missed the
    next test's connection on macOS — a quirk of the fake, not the client.)"""
    holder = {}
    running = FakeTrueNAS("obsidian", COMPOSE)
    # Every connection is served by whichever fake the current test set up.
    running.handle = lambda ws: holder["fake"].handle(ws)
    server = running.serve(0, tls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield holder, f"127.0.0.1:{server.socket.getsockname()[1]}"
    server.shutdown()


@pytest.fixture
def truenas(server):
    holder, host = server
    holder["fake"] = FakeTrueNAS("obsidian", COMPOSE, job_seconds=0.1)
    return holder["fake"], host


def client(host, key=API_KEY):
    return TrueNASClient(host=host, username=API_USER, api_key=key, verify_tls=False, timeout=5)


def test_signs_in_with_the_api_key_and_reads_the_app(truenas):
    fake, host = truenas
    with client(host) as api:
        assert api.app_config("obsidian") == COMPOSE
    assert fake.logins == [{"mechanism": "API_KEY_PLAIN", "username": API_USER, "api_key": API_KEY}]


def test_a_refused_key_is_an_error_not_a_session(truenas):
    _, host = truenas
    with pytest.raises(TrueNASError, match="didn't accept"):
        with client(host, key="wrong"):
            pass


def test_a_method_error_carries_truenas_reason(truenas):
    _, host = truenas
    with client(host) as api, pytest.raises(TrueNASError, match="nope not found"):
        api.app_config("nope")


def test_an_update_runs_as_a_job_followed_to_the_end(truenas):
    fake, host = truenas
    moved = {"services": {"backend": {"image": "ghcr.io/thyglizzyglobber/obsidian-backend:1.2.0"}}}
    with client(host) as api:
        job_id = api.start_app_update("obsidian", moved)
        assert job_id > 7  # newer than every job there was before
        job = api.wait_for_job(job_id, timeout=5)
    assert job["state"] == "SUCCESS"
    assert fake.updates == [moved]


def test_a_failed_job_says_why(truenas):
    fake, host = truenas

    def refuse(_compose):
        raise RuntimeError("image pull failed")

    fake.on_update = refuse
    with client(host) as api:
        job = api.wait_for_job(api.start_app_update("obsidian", COMPOSE), timeout=5)
    assert job["state"] == "FAILED"
    assert "image pull failed" in job["error"]


def test_nothing_listening_is_an_error(monkeypatch):
    monkeypatch.setattr("app.truenas.CONNECT_RETRY_SECONDS", 0)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(TrueNASError, match="couldn't reach"):
        with client(f"127.0.0.1:{port}"):
            pass


def test_no_key_set_up_is_said_plainly(monkeypatch):
    monkeypatch.setattr("app.config.TRUENAS_API_KEY", None)
    with pytest.raises(TrueNASError, match="no TrueNAS API key"):
        with TrueNASClient(host="127.0.0.1", username=API_USER):
            pass


def test_the_default_gateway_comes_from_the_route_table(tmp_path):
    table = tmp_path / "route"
    table.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    )
    assert default_gateway(table) == "172.18.0.1"
    assert default_gateway(tmp_path / "missing") is None
