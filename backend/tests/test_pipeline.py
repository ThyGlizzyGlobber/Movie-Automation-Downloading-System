import re

import pytest

from app import config
from app.pipeline import download
from app.qbt import QBTError

_BTIH_RE = re.compile(r"btih:([a-zA-Z0-9]+)")


@pytest.fixture(autouse=True)
def _fast_hash_capture(monkeypatch):
    """Real hash capture retries with real sleeps (see config.py) — not
    something unit tests should wait through. One immediate attempt is
    enough since these fakes never need a retry to see their own write."""
    monkeypatch.setattr(config, "HASH_CAPTURE_ATTEMPTS", 1)
    monkeypatch.setattr(config, "HASH_CAPTURE_INTERVAL_SECONDS", 0)

MOVIE = {
    "title": "Dune: Part Two",
    "original_title": "Dune: Part Two",
    "release_date": "2024-03-01",
}


class FakeTMDBClient:
    def get_movie(self, tmdb_id):
        return MOVIE


class FakeQBTClient:
    """Records calls; `results_by_variant` maps a query string to the raw
    search rows that variant should return (default: no results)."""

    def __init__(self, results_by_variant=None, existing_hashes=None, free_space_bytes=1_000_000_000_000):
        self.results_by_variant = results_by_variant or {}
        self._existing_hashes = existing_hashes or set()
        self._free_space_bytes = free_space_bytes
        self.searched_variants: list[str] = []
        self.added: list[tuple[str, str]] = []
        self.ensured_categories: list[str] = []

    def search(self, pattern, category="movies", plugins="enabled"):
        self.searched_variants.append(pattern)
        return self.results_by_variant.get(pattern, [])

    def existing_torrent_hashes(self):
        # A snapshot copy, like the real client's fresh `{t.hash ...}` set
        # comprehension each call — callers that stash an earlier snapshot
        # (pipeline.py's hash-diff capture) must see it as unaffected by a
        # later add_torrent() mutating our internal set.
        return set(self._existing_hashes)

    def free_space_bytes(self):
        return self._free_space_bytes

    def ensure_category(self, category):
        self.ensured_categories.append(category)

    def add_torrent(self, file_url, category):
        self.added.append((file_url, category))
        # Mimics qBittorrent indexing the new torrent: a magnet's hash
        # becomes visible in existing_torrent_hashes() right after adding.
        # A non-magnet fileUrl doesn't carry a hash to surface this way.
        match = _BTIH_RE.search(file_url)
        if match:
            self._existing_hashes.add(match.group(1).lower())


def _result(**overrides):
    base = {
        "engineName": "piratebay",
        "fileName": "Dune.Part.Two.2024.2160p.REMUX.mkv",
        "fileUrl": "magnet:?xt=urn:btih:AAAA",
        "fileSize": 40_000_000_000,
        "nbSeeders": 100,
    }
    base.update(overrides)
    return base


def test_download_adds_winner_from_first_variant_that_has_candidates():
    qbt = FakeQBTClient(results_by_variant={"Dune: Part Two": [_result()]})

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.variant_used == "Dune: Part Two"
    assert qbt.added == [("magnet:?xt=urn:btih:AAAA", "movies")]
    assert qbt.ensured_categories == ["movies"]


def test_download_falls_back_to_next_variant_when_first_has_no_candidates():
    qbt = FakeQBTClient(
        results_by_variant={
            "Dune: Part Two": [],  # no results at all for the canonical title
            "Dune": [_result(fileUrl="magnet:?xt=urn:btih:BBBB")],
        }
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.variant_used == "Dune"
    assert qbt.searched_variants == ["Dune: Part Two", "Dune"]


def test_download_no_qualifying_results_when_every_variant_comes_up_empty():
    qbt = FakeQBTClient(results_by_variant={})

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "no qualifying results"
    assert qbt.added == []


def test_download_skips_candidate_that_fails_relevance_gate():
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileName="Some.Other.Movie.2024.2160p.REMUX.mkv")]}
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "no qualifying results"


def test_download_excludes_candidate_already_present_in_qbittorrent():
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileUrl="magnet:?xt=urn:btih:AAAA")]},
        existing_hashes={"aaaa"},
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "no qualifying results"
    assert qbt.added == []


def test_download_insufficient_free_space_when_only_candidate_is_too_large():
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileSize=90_000_000_000)]},
        free_space_bytes=10_000_000_000,
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "insufficient free space"
    assert qbt.added == []


def test_download_picks_smaller_candidate_when_best_ranked_does_not_fit_free_space():
    too_big = _result(
        fileName="Dune.Part.Two.2024.2160p.REMUX.mkv", fileSize=90_000_000_000, fileUrl="magnet:?xt=urn:btih:CCCC"
    )
    fits = _result(
        fileName="Dune.Part.Two.2024.2160p.WEBRip.mkv", fileSize=10_000_000_000, fileUrl="magnet:?xt=urn:btih:DDDD"
    )
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [too_big, fits]},
        free_space_bytes=20_000_000_000,
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.winner["fileUrl"] == "magnet:?xt=urn:btih:DDDD"


# ---------------------------------------------------------------------------
# Resolution floor / fallback — lowering config.MIN_RESOLUTION is what lets
# the pipeline add a 1080p release when no 4K one qualifies.
# ---------------------------------------------------------------------------


def test_download_does_not_fall_back_to_1080p_by_default():
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileName="Dune.Part.Two.2024.1080p.WEBRip.mkv")]}
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "no qualifying results"
    assert qbt.added == []


def test_download_falls_back_to_1080p_when_floor_lowered(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileName="Dune.Part.Two.2024.1080p.WEBRip.mkv")]}
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.score.resolution_score == 3


def test_download_prefers_2160p_over_1080p_when_floor_lowered_and_both_present(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    uhd = _result(fileName="Dune.Part.Two.2024.2160p.WEBRip.mkv", fileUrl="magnet:?xt=urn:btih:UHD")
    fhd = _result(fileName="Dune.Part.Two.2024.1080p.REMUX.mkv", fileUrl="magnet:?xt=urn:btih:FHD")
    qbt = FakeQBTClient(results_by_variant={"Dune: Part Two": [fhd, uhd]})

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.winner["fileUrl"] == "magnet:?xt=urn:btih:UHD"


# ---------------------------------------------------------------------------
# Torrent hash capture — Stage 3's download watcher needs to know which
# torrent a request added, to poll it through to "complete".
# ---------------------------------------------------------------------------


def test_download_captures_hash_of_newly_added_torrent():
    qbt = FakeQBTClient(results_by_variant={"Dune: Part Two": [_result(fileUrl="magnet:?xt=urn:btih:EEEE")]})

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.torrent_hash == "eeee"


def test_download_returns_add_failed_when_it_never_actually_lands():
    """The real bug this closes (found live: a limetorrents link that
    qBittorrent accepted but never actually fetched, leaving a request
    stuck showing "downloading" with nothing in qBittorrent at all). This
    fake's fileUrl never surfaces a hash in existing_torrent_hashes() no
    matter how many retries — zero new hashes after exhausting the retry
    budget is the real "never landed" signal, distinct from the ambiguous
    (more than one new hash) case below. With only one candidate on the
    table, there's nothing to fall back to, so this is the terminal
    "add failed" status rather than a fallback to a next-best candidate."""
    qbt = FakeQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileUrl="https://example.com/dune.torrent")]}
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "add failed"
    assert "never actually added" in result.error
    assert result.winner["fileUrl"] == "https://example.com/dune.torrent"


def test_download_leaves_hash_unset_when_capture_is_ambiguous():
    """More than one new hash (e.g. a concurrent manual add elsewhere)
    means something was clearly added — just not identifiably ours — so
    this must return "added" with an untracked hash, not raise."""

    class ConcurrentAddQBTClient(FakeQBTClient):
        def add_torrent(self, file_url, category):
            super().add_torrent(file_url, category)
            self._existing_hashes.add("someone-elses-hash")

    qbt = ConcurrentAddQBTClient(results_by_variant={"Dune: Part Two": [_result(fileUrl="magnet:?xt=urn:btih:EEEE")]})

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.torrent_hash is None


def test_download_returns_add_failed_rather_than_reporting_added():
    """A dead/unreachable candidate link makes qBittorrent's own
    torrents_add() fail (QBTClient.add_torrent raises QBTError for that —
    see qbt.py). download() must not swallow it and claim "added" with
    nothing actually added — the caller (worker.py) marks the request
    "failed" instead of leaving it stuck at "downloading" forever. Only one
    candidate here, so there's nothing to fall back to (see the next test
    for the case where there is)."""

    class RejectingQBTClient(FakeQBTClient):
        def add_torrent(self, file_url, category):
            raise QBTError(f"qBittorrent rejected the add ('Fails.'): {file_url}")

    qbt = RejectingQBTClient(results_by_variant={"Dune: Part Two": [_result()]})

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "add failed"
    assert "rejected the add" in result.error


def test_download_falls_back_to_next_candidate_when_add_fails():
    """The real case that motivated this: the #1-ranked candidate's link
    is dead (e.g. a scraper listing something qBittorrent can never
    fetch). With other candidates still on the table, the pipeline must
    try the next-best one rather than failing the whole request."""
    bad = _result(fileUrl="magnet:?xt=urn:btih:AAAA", fileName="Dune.Part.Two.2024.2160p.REMUX-BADGROUP.mkv", nbSeeders=100)
    good = _result(
        fileUrl="magnet:?xt=urn:btih:BBBB",
        fileName="Dune.Part.Two.2024.2160p.REMUX-GOODGROUP.mkv",
        fileSize=39_000_000_000,
        nbSeeders=50,
    )

    class PartlyRejectingQBTClient(FakeQBTClient):
        def add_torrent(self, file_url, category):
            if file_url == "magnet:?xt=urn:btih:AAAA":
                raise QBTError("qBittorrent rejected the add ('Fails.')")
            super().add_torrent(file_url, category)

    qbt = PartlyRejectingQBTClient(results_by_variant={"Dune: Part Two": [bad, good]})

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.torrent_hash == "bbbb"
    assert qbt.added == [("magnet:?xt=urn:btih:BBBB", "movies")]


def test_download_retries_hash_capture_until_torrent_is_indexed(monkeypatch):
    """Real qBittorrent doesn't index a direct-.torrent-URL result
    instantly — confirmed live against the real NAS during Stage 3
    validation (a torlock winner's hash was missing on the first check,
    present about a minute later). The capture loop must keep checking
    instead of giving up after a single look."""
    monkeypatch.setattr(config, "HASH_CAPTURE_ATTEMPTS", 3)

    class DelayedIndexQBTClient(FakeQBTClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pending_hash = None
            self._checks_since_add = 0

        def add_torrent(self, file_url, category):
            self.added.append((file_url, category))
            self._pending_hash = "ffff"  # not visible in existing_torrent_hashes() yet

        def existing_torrent_hashes(self):
            if self._pending_hash:
                self._checks_since_add += 1
                if self._checks_since_add >= 2:  # becomes visible on the second check
                    self._existing_hashes.add(self._pending_hash)
                    self._pending_hash = None
            return set(self._existing_hashes)

    qbt = DelayedIndexQBTClient(
        results_by_variant={"Dune: Part Two": [_result(fileUrl="https://example.com/dune.torrent")]}
    )

    result = download(693134, FakeTMDBClient(), qbt)

    assert result.status == "added"
    assert result.torrent_hash == "ffff"
