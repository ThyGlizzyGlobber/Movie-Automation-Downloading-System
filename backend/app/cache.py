"""In-memory TTL response cache, sitting in front of TMDB's popular,
discover-by-provider, and provider-list calls (Stage 1 scope — not search
or single-title lookups, which are per-query)."""

import functools
import time
from threading import Lock


class TTLCache:
    # A per-title key (release dates, recommendations) is often never read
    # again, so waiting for a re-read to evict it let the cache grow for
    # as long as the process ran. Every SWEEP_EVERY sets drops whatever has
    # expired; MAX_ENTRIES bounds what's left, oldest first.
    SWEEP_EVERY = 256
    MAX_ENTRIES = 4096

    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._store: dict[tuple, tuple[float, object]] = {}
        self._lock = Lock()
        self._sets_since_sweep = 0

    def get(self, key: tuple):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, False
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._store[key]
                return None, False
            return value, True

    def set(self, key: tuple, value: object) -> None:
        with self._lock:
            now = time.monotonic()
            # Re-inserted rather than overwritten, so dict order stays
            # oldest-first for the size cap below.
            self._store.pop(key, None)
            self._store[key] = (now + self.ttl_seconds, value)
            self._sets_since_sweep += 1
            if self._sets_since_sweep >= self.SWEEP_EVERY or len(self._store) > self.MAX_ENTRIES:
                self._sets_since_sweep = 0
                for stale in [k for k, (expires_at, _) in self._store.items() if now >= expires_at]:
                    del self._store[stale]
                while len(self._store) > self.MAX_ENTRIES:
                    del self._store[next(iter(self._store))]

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


def ttl_cache(ttl_seconds: float):
    """Decorator caching a method's return value, keyed on its args/kwargs.
    Each decorated callable gets its own cache instance."""

    def decorator(func):
        cache = TTLCache(ttl_seconds)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            value, hit = cache.get(key)
            if hit:
                return value
            value = func(*args, **kwargs)
            cache.set(key, value)
            return value

        wrapper.cache = cache
        return wrapper

    return decorator
