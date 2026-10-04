import threading
import time

from app import cache
from app.cache import TTLCache, ttl_cache


def test_expired_entries_are_swept_on_set_even_if_never_read_again(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    store = TTLCache(ttl_seconds=10)
    for n in range(TTLCache.SWEEP_EVERY - 1):
        store.set((n,), n)
    clock[0] = 11.0

    store.set(("fresh",), "fresh")

    assert len(store) == 1
    assert store.get(("fresh",)) == ("fresh", True)


def test_size_is_capped_oldest_first(monkeypatch):
    monkeypatch.setattr(TTLCache, "MAX_ENTRIES", 3)
    store = TTLCache(ttl_seconds=60)
    for n in range(5):
        store.set((n,), n)

    assert len(store) == 3
    assert store.get((0,)) == (None, False)
    assert store.get((1,)) == (None, False)
    assert store.get((4,)) == (4, True)


def test_ttl_cache_still_serves_hits_within_the_ttl():
    calls = []

    @ttl_cache(60)
    def lookup(tmdb_id):
        calls.append(tmdb_id)
        return tmdb_id * 2

    assert lookup(1) == 2
    assert lookup(1) == 2
    assert calls == [1]


def test_a_stale_entry_is_served_at_once_and_refreshed_behind(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    answers = iter(["old", "new"])
    refreshed = threading.Event()

    @ttl_cache(60, stale_seconds=3600)
    def lookup():
        value = next(answers)
        if value == "new":
            refreshed.set()
        return value

    assert lookup() == "old"
    clock[0] = 61.0
    assert lookup() == "old"  # past the TTL, inside the stale window: no wait
    assert refreshed.wait(5)
    for _ in range(100):
        if lookup.cache.get(((), ()))[1]:
            break
        time.sleep(0.01)
    assert lookup() == "new"


def test_a_stale_entry_past_its_window_is_fetched_again(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    calls = []

    @ttl_cache(60, stale_seconds=100)
    def lookup():
        calls.append(clock[0])
        return len(calls)

    assert lookup() == 1
    clock[0] = 200.0
    assert lookup() == 2
    assert calls == [0.0, 200.0]


def test_simultaneous_misses_share_one_fetch():
    calls = []
    started = threading.Event()
    release = threading.Event()

    @ttl_cache(60)
    def lookup():
        calls.append(1)
        started.set()
        release.wait(5)
        return "answer"

    results = []
    threads = [threading.Thread(target=lambda: results.append(lookup())) for _ in range(5)]
    threads[0].start()
    assert started.wait(5)
    for t in threads[1:]:
        t.start()
    release.set()
    for t in threads:
        t.join(5)

    assert results == ["answer"] * 5
    assert calls == [1]
