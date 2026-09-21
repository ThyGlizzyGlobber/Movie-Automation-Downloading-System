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


def test_ensure_downloaded_asks_for_a_codec_apple_can_play(_cache_dir, monkeypatch):
    """VP9 in an .mp4 is what YouTube's "best" gives you and what an
    iPhone refuses to play — silently, because the container claims mp4.
    The selector must ask for H.264 and AAC by name, and must still fall
    back rather than come away with nothing."""
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            (config.TRAILER_CACHE_DIR / "movie-1-key.mp4").write_bytes(b"x")

    monkeypatch.setattr(trailers.yt_dlp, "YoutubeDL", FakeYDL)
    trailers.ensure_downloaded("movie", 1, "key")

    fmt = captured["format"]
    assert "vcodec^=avc1" in fmt
    assert "acodec^=mp4a" in fmt
    # Still degrades rather than failing outright for a VP9-only video.
    assert fmt.count("/") >= 2
    assert captured["merge_output_format"] == "mp4"


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


# ---------------------------------------------------------------------------
# pick_shortest_suitable / resolve — which of a title's clips the hero
# plays. Type is a poor guide to length (a "Teaser" is as often a 5s
# social sting as a short trailer), so these measure.
# ---------------------------------------------------------------------------


def _videos(*keys):
    return [{"key": k, "site": "YouTube", "type": "Trailer"} for k in keys]


def _durations(monkeypatch, by_key):
    monkeypatch.setattr(trailers, "probe_duration", lambda key: by_key.get(key))


def test_pick_shortest_suitable_takes_the_shortest_above_the_floor(monkeypatch):
    monkeypatch.setattr(config, "TRAILER_MIN_SECONDS", 45)
    _durations(monkeypatch, {"long": 136, "mid": 61, "sting": 15})
    assert trailers.pick_shortest_suitable(_videos("long", "mid", "sting")) == "mid"


def test_pick_shortest_suitable_ignores_social_stings(monkeypatch):
    """Inside Out 2, live: four official 15-30s teasers that are
    marketing bumpers, against one 98s trailer that is the only real
    preview. Shortest-wins without a floor picks a bumper."""
    monkeypatch.setattr(config, "TRAILER_MIN_SECONDS", 45)
    _durations(monkeypatch, {"announce": 98, "fresh": 15, "celebrate": 30, "fuzzies": 15})
    assert trailers.pick_shortest_suitable(_videos("announce", "fresh", "celebrate", "fuzzies")) == "announce"


def test_pick_shortest_suitable_falls_back_to_the_longest_when_all_are_short(monkeypatch):
    """A title with nothing but stings still gets one — something beats
    a still poster."""
    monkeypatch.setattr(config, "TRAILER_MIN_SECONDS", 45)
    _durations(monkeypatch, {"a": 5, "b": 21, "c": 20})
    assert trailers.pick_shortest_suitable(_videos("a", "b", "c")) == "b"


def test_pick_shortest_suitable_keeps_the_type_guess_when_nothing_measures(monkeypatch):
    _durations(monkeypatch, {})
    assert trailers.pick_shortest_suitable(_videos("first", "second")) == "first"


def test_pick_shortest_suitable_skips_unmeasurable_candidates(monkeypatch):
    monkeypatch.setattr(config, "TRAILER_MIN_SECONDS", 45)
    _durations(monkeypatch, {"measurable": 90})
    assert trailers.pick_shortest_suitable(_videos("blocked", "measurable")) == "measurable"


def test_pick_shortest_suitable_measures_at_most_the_probe_limit(monkeypatch):
    monkeypatch.setattr(config, "TRAILER_PROBE_LIMIT", 2)
    probed = []

    def probe(key):
        probed.append(key)
        return 60

    monkeypatch.setattr(trailers, "probe_duration", probe)
    trailers.pick_shortest_suitable(_videos("a", "b", "c", "d"))
    assert probed == ["a", "b"]


def test_pick_shortest_suitable_returns_none_without_candidates():
    assert trailers.pick_shortest_suitable([]) is None


def test_resolve_uses_a_cached_clip_without_measuring_anything(_cache_dir, monkeypatch):
    """The probing is once per title: whichever clip won last time is on
    disk under its own key, so the next request has to recognise it
    among the candidates rather than measuring them all again."""
    _cache_dir.mkdir(parents=True, exist_ok=True)
    (_cache_dir / "movie-42-mid.mp4").write_bytes(b"cached")
    monkeypatch.setattr(trailers, "probe_duration", lambda key: pytest.fail("probed a title already on disk"))

    path = trailers.resolve("movie", 42, _videos("long", "mid"))

    assert path == _cache_dir / "movie-42-mid.mp4"


def test_resolve_downloads_the_measured_pick(_cache_dir, monkeypatch):
    monkeypatch.setattr(config, "TRAILER_MIN_SECONDS", 45)
    _durations(monkeypatch, {"long": 136, "mid": 61})
    asked = []
    monkeypatch.setattr(trailers, "ensure_downloaded", lambda mt, tid, key: asked.append(key) or _cache_dir / f"{key}.mp4")

    trailers.resolve("movie", 42, _videos("long", "mid"))

    assert asked == ["mid"]


def test_resolve_returns_none_without_candidates(_cache_dir):
    assert trailers.resolve("movie", 42, []) is None
