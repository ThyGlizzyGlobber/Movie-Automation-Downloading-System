"""qBittorrent client wrapper: search executor, category management, adding
torrents, and existing-torrent/free-space lookups. Talks only to the
qBittorrent WebUI API over the network — no Docker socket, no other
privileged access."""

import time

import qbittorrentapi

SEARCH_POLL_INTERVAL_SECONDS = 2
SEARCH_CEILING_SECONDS = 55


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
        self._client.torrents_add(urls=file_url, category=category)
