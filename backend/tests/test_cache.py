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
