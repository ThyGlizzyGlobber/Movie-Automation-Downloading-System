import io

import pytest
from PIL import Image

from app import config, logos

NAME = "abcdEFGH1234.png"


def _png(width: int, height: int) -> bytes:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(0, width, 7):
        for y in range(height // 4, height // 2):
            image.putpixel((x, y), (230, 20, 20, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class _Upstream:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class _InlinePool:
    def submit(self, fn, *args):
        from concurrent.futures import Future

        future = Future()
        future.set_result(fn(*args))
        return future


@pytest.fixture
def logo_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOGO_CACHE_DIR", tmp_path)
    monkeypatch.setattr(logos, "_pool", _InlinePool())
    logos._failed.clear()
    logos._in_flight.clear()
    return tmp_path


def test_a_logo_is_fitted_to_the_banner_and_stored_as_webp(logo_dir, monkeypatch):
    fetched = []
    monkeypatch.setattr(logos.requests, "get", lambda url, timeout: fetched.append(url) or _Upstream(_png(4000, 1200)))

    path = logos.get(NAME)

    assert path == logo_dir / "abcdEFGH1234.webp"
    stored = Image.open(path)
    assert stored.format == "WEBP"
    assert stored.size[0] <= logos.MAX_WIDTH and stored.size[1] <= logos.MAX_HEIGHT
    assert stored.mode == "RGBA"
    assert fetched == [logos.ORIGINAL_SOURCES[0] + NAME]
    # Once on disk, nothing is fetched again.
    assert logos.get(NAME) == path
    assert len(fetched) == 1


def test_a_small_logo_is_never_scaled_up(logo_dir, monkeypatch):
    monkeypatch.setattr(logos.requests, "get", lambda url, timeout: _Upstream(_png(400, 60)))
    assert Image.open(logos.get(NAME)).size == (400, 60)


def test_the_original_comes_from_tmdb_when_the_local_cache_cannot_answer(logo_dir, monkeypatch):
    fetched = []

    def get(url, timeout):
        fetched.append(url)
        if url.startswith(logos.ORIGINAL_SOURCES[0]):
            raise ConnectionError("no frontend here")
        return _Upstream(_png(200, 80))

    monkeypatch.setattr(logos.requests, "get", get)
    assert logos.get(NAME) is not None
    assert fetched == [logos.ORIGINAL_SOURCES[0] + NAME, logos.TMDB_ORIGINAL_URL + NAME]


def test_ffmpeg_encodes_when_pillow_cannot(logo_dir, monkeypatch):
    monkeypatch.setattr(logos.requests, "get", lambda url, timeout: _Upstream(_png(100, 50)))
    monkeypatch.setattr(logos, "_encode_with_pillow", lambda data: None)
    monkeypatch.setattr(logos, "_encode_with_ffmpeg", lambda data: b"RIFF-from-ffmpeg")

    assert logos.get(NAME).read_bytes() == b"RIFF-from-ffmpeg"


def test_a_logo_that_cannot_be_encoded_is_remembered_not_retried(logo_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(logos.requests, "get", lambda url, timeout: calls.append(url) or _Upstream(b"not an image"))
    monkeypatch.setattr(logos, "_encode_with_ffmpeg", lambda data: None)

    assert logos.get(NAME) is None
    assert logos.get(NAME) is None
    assert len(calls) == 1


def test_only_tmdb_png_logo_names_are_accepted():
    assert logos.is_logo_name("4XkF0Rf7gvSfea8fYLFbU5tmuJw.png")
    assert not logos.is_logo_name("../etc/passwd.png")
    assert not logos.is_logo_name("4XkF0Rf7gvSfea8fYLFbU5tmuJw.svg")
    assert not logos.is_logo_name("x.png")


def test_warm_starts_an_encode_without_waiting_and_skips_what_is_on_disk(logo_dir, monkeypatch):
    built = []
    monkeypatch.setattr(logos, "_build", lambda name: built.append(name))
    logos.warm("/" + NAME)
    assert built == [NAME]

    (logo_dir / "abcdEFGH1234.webp").write_bytes(b"done")
    logos.warm("/" + NAME)
    logos.warm(None)
    assert built == [NAME]
