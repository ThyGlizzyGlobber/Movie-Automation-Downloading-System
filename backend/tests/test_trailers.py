"""trailers.py — the home hero carousel's self-hosted trailer cache. Never
performs a real download: yt_dlp.YoutubeDL is monkeypatched out in every
test, either simulating a successful download (by writing the expected
dest file) or a failure."""

import pytest

from app import config, trailers


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRAILER_CACHE_DIR", tmp_path / "trailers")
    monkeypatch.setattr(config, "TRAILER_CACHE_MAX_FILES", 20)
    return tmp_path / "trailers"


def test_cached_trailer_path_matches_naming_scheme(_cache_dir):
    path = trailers.cached_trailer_path("movie", 693134, "abc123")

    assert path == _cache_dir / "movie-693134-abc123.mp4"


def test_ensure_downloaded_returns_existing_file_without_downloading(_cache_dir, monkeypatch):
    _cache_dir.mkdir(parents=True)
    dest = _cache_dir / "movie-693134-abc123.mp4"
    dest.write_bytes(b"already cached")

    def _boom(*args, **kwargs):
        raise AssertionError("should not attempt a download when the file already exists")

    monkeypatch.setattr(trailers.yt_dlp, "YoutubeDL", _boom)

    result = trailers.ensure_downloaded("movie", 693134, "abc123")

    assert result == dest


def test_ensure_downloaded_downloads_and_returns_path(_cache_dir, monkeypatch):
    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            (_cache_dir / "movie-693134-abc123.mp4").write_bytes(b"downloaded bytes")

    monkeypatch.setattr(trailers.yt_dlp, "YoutubeDL", _FakeYDL)

    result = trailers.ensure_downloaded("movie", 693134, "abc123")

    assert result == _cache_dir / "movie-693134-abc123.mp4"
    assert result.read_bytes() == b"downloaded bytes"


def test_ensure_downloaded_returns_none_and_cleans_up_on_exception(_cache_dir, monkeypatch):
    class _FailingYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            raise RuntimeError("network error")

    monkeypatch.setattr(trailers.yt_dlp, "YoutubeDL", _FailingYDL)

    result = trailers.ensure_downloaded("movie", 693134, "abc123")

    assert result is None
    assert not (_cache_dir / "movie-693134-abc123.mp4").exists()


def test_ensure_downloaded_returns_none_when_download_silently_produces_no_file(_cache_dir, monkeypatch):
    class _NoopYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            pass

    monkeypatch.setattr(trailers.yt_dlp, "YoutubeDL", _NoopYDL)

    result = trailers.ensure_downloaded("movie", 693134, "abc123")

    assert result is None


def test_enforce_cache_retention_evicts_oldest_beyond_max(_cache_dir, monkeypatch):
    monkeypatch.setattr(config, "TRAILER_CACHE_MAX_FILES", 2)
    _cache_dir.mkdir(parents=True)
    import os
    import time

    for i, name in enumerate(["a.mp4", "b.mp4", "c.mp4"]):
        p = _cache_dir / name
        p.write_bytes(b"x")
        mtime = time.time() + i
        os.utime(p, (mtime, mtime))

    trailers.enforce_cache_retention()

    remaining = {p.name for p in _cache_dir.glob("*.mp4")}
    assert remaining == {"b.mp4", "c.mp4"}


def test_enforce_cache_retention_noop_when_dir_missing(_cache_dir):
    trailers.enforce_cache_retention()  # should not raise


def test_enforce_cache_retention_noop_when_under_limit(_cache_dir):
    _cache_dir.mkdir(parents=True)
    (_cache_dir / "a.mp4").write_bytes(b"x")

    trailers.enforce_cache_retention()

    assert {p.name for p in _cache_dir.glob("*.mp4")} == {"a.mp4"}
