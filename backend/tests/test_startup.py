"""The backend's start-up with qBittorrent not there yet — the usual case on
a box, where everything powers up at once and qBittorrent waits on the VPN.
It keeps trying, and starts downloads the moment qBittorrent answers."""

import asyncio
from types import SimpleNamespace

from app import config
from app.api import main


def test_qbittorrent_late_to_start_is_picked_up_without_a_restart(monkeypatch):
    attempts = []

    class LateQBT:
        def __init__(self, host, port, username, password):
            attempts.append((host, port))
            if len(attempts) < 3:
                raise ConnectionError("connection refused")

    started = []

    class FakeWorker:
        def __init__(self, store, tmdb, qbt):
            self.qbt = qbt

        async def start(self):
            started.append(self.qbt)

    monkeypatch.setattr(main, "QBTClient", LateQBT)
    monkeypatch.setattr(main, "Worker", FakeWorker)
    monkeypatch.setattr(main, "QBT_RETRY_SECONDS", 0)
    monkeypatch.setattr(
        config, "resolve_qbt_config", lambda store: config.QbtConnection("gluetun", 8080, "admin", "pw")
    )
    app = SimpleNamespace(state=SimpleNamespace(qbt=None, worker=None, tmdb=object()))

    asyncio.run(main._connect_qbt_when_ready(app, store=object()))

    assert attempts == [("gluetun", 8080)] * 3
    assert isinstance(app.state.qbt, LateQBT)
    assert started == [app.state.qbt]
