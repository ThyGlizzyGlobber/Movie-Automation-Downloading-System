"""qBittorrent client wrapper: search executor, category management, adding
torrents, and existing-torrent/free-space lookups. Talks only to the
qBittorrent WebUI API over the network — no Docker socket, no other
privileged access."""

import time

import qbittorrentapi

SEARCH_POLL_INTERVAL_SECONDS = 2
SEARCH_CEILING_SECONDS = 55


class QBTError(RuntimeError):
    pass


class QBTClient:
    def __init__(self, host: str, port: int, username: str = "", password: str = ""):
        self._client = qbittorrentapi.Client(host=host, port=port, username=username, password=password)
        self._client.auth_log_in()

    def search(self, pattern: str, category: str = "movies", plugins: str = "enabled") -> list[dict]:
        """Runs one search job to completion (or the time ceiling), always
        deleting the job in `finally`. Returns the raw result rows."""
        job = self._client.search.start(pattern=pattern, plugins=plugins, category=category)
        try:
            elapsed = 0.0
            while elapsed < SEARCH_CEILING_SECONDS:
                status = job.status()[0]
                if status["status"] == "Stopped":
                    break
                time.sleep(SEARCH_POLL_INTERVAL_SECONDS)
                elapsed += SEARCH_POLL_INTERVAL_SECONDS
            return list(job.results().get("results", []))
        finally:
            job.delete()

    def ping(self) -> bool:
        """Cheap, read-only reachability check for /api/health — this
        client already authenticated at construction time, but that only
        proves qBittorrent was up at *startup*; a healthcheck needs to know
        it's still reachable right now (network blip, qBittorrent restart)."""
        try:
            self._client.app_version()
            return True
        except Exception:
            return False

    def existing_torrent_hashes(self) -> set[str]:
        """Infohashes already present in qBittorrent, for dedup against a
        fresh search result set."""
        return {t.hash.lower() for t in self._client.torrents_info()}

    def free_space_bytes(self) -> int:
        return self._client.sync_maindata().get("server_state", {}).get("free_space_on_disk", 0)

    def ensure_category(self, category: str) -> None:
        if category not in self._client.torrents_categories():
            self._client.torrents_create_category(name=category)

    def add_torrent(self, file_url: str, category: str) -> None:
        """`torrents_add` reports its outcome two different ways depending
        on server version: a plain "Ok."/"Fails." string (older), or
        richer per-torrent metadata for WebAPI 2.14+ — {added_torrent_ids,
        success_count, pending_count, failure_count} — confirmed live
        against this household's real instance (qBittorrent v5.2.3, WebAPI
        2.15.1), which always takes this second form.

        Only a *confirmed* synchronous failure is trustworthy here: a
        direct .torrent URL (the majority of real winners) is still
        "pending" — not yet "success" — the instant this call returns,
        since qBittorrent hasn't finished fetching it yet. Confirmed live:
        a torlock winner came back `{'failure_count': 0, 'pending_count':
        1, 'success_count': 0}` and was genuinely present in qBittorrent
        moments later. Treating "pending" as a failure here would reject
        real successes — whether a pending add actually lands is verified
        for real by pipeline.py's `_capture_new_hash` retry loop, not
        synchronously here."""
        result = self._client.torrents_add(urls=file_url, category=category)
        if isinstance(result, str):
            if result.strip() != "Ok.":
                raise QBTError(f"qBittorrent rejected the add ({result!r}): {file_url}")
        elif getattr(result, "failure_count", 0):
            raise QBTError(f"qBittorrent rejected the add ({dict(result)!r}): {file_url}")

    def torrent_info(self, torrent_hash: str) -> dict | None:
        """A single torrent's current state, for Stage 3's download-progress
        watcher. `None` if it's gone (e.g. removed manually)."""
        results = self._client.torrents_info(torrent_hashes=torrent_hash)
        return dict(results[0]) if results else None

    def delete_torrent(self, torrent_hash: str, delete_files: bool = True) -> None:
        """Cancel a request from this app's own UI: removes the torrent
        from qBittorrent and, by default, its downloaded files/folder too —
        not just a queue entry, the actual media on disk."""
        self._client.torrents_delete(delete_files=delete_files, torrent_hashes=torrent_hash)
