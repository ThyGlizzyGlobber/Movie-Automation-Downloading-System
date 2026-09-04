"""qbt.py's add_torrent() success/failure check — the rest of QBTClient is
a thin qbittorrent-api pass-through, validated live against the real NAS
rather than unit tested (see project.md's Stage 2/3 manual validation
runs)."""

import pytest

from app.qbt import QBTClient, QBTError


class FakeMetadata(dict):
    """Stands in for qbittorrentapi's TorrentsAddedMetadata: dict-like
    (qbt.py's error path does `dict(result)`) but also attribute-accessible
    (`getattr(result, "failure_count", 0)`), matching the real object."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for key, value in kwargs.items():
            setattr(self, key, value)


def _client_with_fake_underlying(add_torrent_return):
    client = object.__new__(QBTClient)  # skip __init__'s real auth_log_in()
    client._client = type("FakeUnderlyingClient", (), {"torrents_add": lambda self, **kwargs: add_torrent_return})()
    return client


# -- older qBittorrent: plain "Ok."/"Fails." string --


def test_add_torrent_succeeds_on_ok_string():
    client = _client_with_fake_underlying("Ok.")
    client.add_torrent("magnet:?xt=urn:btih:AAAA", category="movies")  # does not raise


def test_add_torrent_raises_on_fails_string():
    client = _client_with_fake_underlying("Fails.")
    with pytest.raises(QBTError):
        client.add_torrent("http://dead-link.example/x.torrent", category="movies")


# -- newer qBittorrent (WebAPI 2.14+, confirmed live against the real NAS
#    at v5.2.3 / WebAPI 2.15.1): per-torrent metadata object --


def test_add_torrent_succeeds_when_metadata_reports_no_failures():
    client = _client_with_fake_underlying(FakeMetadata(added_torrent_ids=["aaaa"], success_count=1, pending_count=0, failure_count=0))
    client.add_torrent("magnet:?xt=urn:btih:AAAA", category="movies")  # does not raise


def test_add_torrent_succeeds_when_metadata_reports_pending_not_failed():
    """The real case found live: a direct .torrent URL (torlock) qBittorrent
    hasn't finished fetching yet reports as "pending", not "success" — that
    must not be treated as a failure. Whether it actually lands is verified
    for real by pipeline.py's hash-capture retry loop, not here."""
    client = _client_with_fake_underlying(
        FakeMetadata(added_torrent_ids=[], success_count=0, pending_count=1, failure_count=0)
    )
    client.add_torrent("https://www.torlock.com/tor/6952888.torrent", category="movies")  # does not raise


def test_add_torrent_raises_when_metadata_reports_a_failure():
    client = _client_with_fake_underlying(
        FakeMetadata(added_torrent_ids=[], success_count=0, pending_count=0, failure_count=1)
    )
    with pytest.raises(QBTError):
        client.add_torrent("http://dead-link.example/x.torrent", category="movies")
