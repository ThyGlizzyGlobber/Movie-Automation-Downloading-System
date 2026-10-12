"""In-memory TTL response cache, sitting in front of TMDB's popular,
discover-by-provider, and provider-list calls, and the per-title detail
lookups the content pages make."""

import functools
import logging
import threading
import time
from threading import Lock

logger = logging.getLogger(__name__)


class TTLCache:
    # A per-title key (release dates, recommendations) is often never read
    # again, so waiting for a re-read to evict it let the cache grow for
    # as long as the process ran. Every SWEEP_EVERY sets drops whatever has
    # expired; MAX_ENTRIES bounds what's left, oldest first.
    SWEEP_EVERY = 256
    MAX_ENTRIES = 4096

    def __init__(self, ttl_seconds: float, stale_seconds: float = 0.0, max_entries: int | None = None):
        """`stale_seconds`: how long past its TTL an entry is still kept,
        for `get_stale` — see ttl_cache. `max_entries` overrides the class
        cap for a cache whose values are large (a full title detail is a
        couple of hundred KB)."""
        self.ttl_seconds = ttl_seconds
        self.stale_seconds = stale_seconds
        self._max_entries = max_entries
        self._store: dict[tuple, tuple[float, object]] = {}
        self._lock = Lock()
        self._sets_since_sweep = 0

    def get(self, key: tuple):
        """`(value, True)` while fresh, `(None, False)` otherwise."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, False
            expires_at, value = entry
            now = time.monotonic()
            if now >= expires_at:
                if now >= expires_at + self.stale_seconds:
                    del self._store[key]
                return None, False
            return value, True

    def get_stale(self, key: tuple):
        """`(value, True)` for an entry past its TTL but still inside the
        stale window, `(None, False)` otherwise."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, False
            expires_at, value = entry
            if time.monotonic() >= expires_at + self.stale_seconds:
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
            cap = self._max_entries or self.MAX_ENTRIES
            if self._sets_since_sweep >= self.SWEEP_EVERY or len(self._store) > cap:
                self._sets_since_sweep = 0
                for gone in [k for k, (expires_at, _) in self._store.items() if now >= expires_at + self.stale_seconds]:
                    del self._store[gone]
                while len(self._store) > cap:
                    del self._store[next(iter(self._store))]

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


def ttl_cache(ttl_seconds: float, stale_seconds: float = 0.0, max_entries: int | None = None):
    """Decorator caching a method's return value, keyed on its args/kwargs.
    Each decorated callable gets its own cache instance.

    One caller fetches a missing key; any others asking for it at the same
    moment wait for that answer rather than each going to TMDB. A cold
    Home asks for the same lists from the hero, the rows and the trending
    calls all at once, and every one of them used to make its own trip.

    With `stale_seconds`, an expired entry still inside that window is
    returned at once and refreshed in the background, so after the first
    fetch of the day nobody waits for TMDB again — the browse rows used to
    cost whoever arrived first after each five-minute expiry 1-3s."""

    def decorator(func):
        cache = TTLCache(ttl_seconds, stale_seconds=stale_seconds, max_entries=max_entries)
        inflight: dict[tuple, Lock] = {}
        refreshing: set[tuple] = set()
        guard = Lock()

        def fetch(key, args, kwargs):
            with guard:
                lock = inflight.setdefault(key, Lock())
            with lock:
                value, hit = cache.get(key)
                if hit:
                    return value
                try:
                    value = func(*args, **kwargs)
                    cache.set(key, value)
                finally:
                    with guard:
                        inflight.pop(key, None)
                return value

        def refresh(key, args, kwargs):
            try:
                fetch(key, args, kwargs)
            except Exception:  # noqa: BLE001 — the stale value stays until it ages out
                logger.warning("background refresh failed for %s", func.__qualname__, exc_info=True)
            finally:
                with guard:
                    refreshing.discard(key)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            value, hit = cache.get(key)
            if hit:
                return value
            if stale_seconds:
                value, found = cache.get_stale(key)
                if found:
                    with guard:
                        start = key not in refreshing
                        refreshing.add(key)
                    if start:
                        threading.Thread(target=refresh, args=(key, args, kwargs), daemon=True).start()
                    return value
            return fetch(key, args, kwargs)

        wrapper.cache = cache
        return wrapper

    return decorator
