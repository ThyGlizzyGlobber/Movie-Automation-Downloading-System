"""In-memory TTL response cache, sitting in front of TMDB's popular,
discover-by-provider, and provider-list calls (Stage 1 scope — not search
or single-title lookups, which are per-query)."""

import functools
import time
from threading import Lock


class TTLCache:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._store: dict[tuple, tuple[float, object]] = {}
        self._lock = Lock()

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
            self._store[key] = (time.monotonic() + self.ttl_seconds, value)

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
