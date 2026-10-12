"""RequestStore itself: one class callers see, assembled from a mixin per
group of tables (requests.py, asks.py, shows.py, library.py,
plex_servers.py, users.py, settings.py) on top of the schema
(schema.py). Each mixin relies on the `_conn` and `_lock` set up here."""

import sqlite3
from pathlib import Path
from threading import RLock

from app.db.asks import StoreAsks
from app.db.connection import SerializedConnection
from app.db.library import StoreLibrary
from app.db.plex_servers import StorePlexServers
from app.db.requests import StoreRequests
from app.db.schema import StoreSchema
from app.db.settings import StoreSettings
from app.db.shows import StoreShows
from app.db.users import StoreUsers


class RequestStore(
    StoreSchema,
    StoreRequests,
    StoreAsks,
    StoreShows,
    StoreLibrary,
    StorePlexServers,
    StoreUsers,
    StoreSettings,
):
    def __init__(self, db_path: str | Path):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Reentrant: the many `with self._lock:` blocks in the mixins wrap
        # calls that take the same lock again inside the connection.
        self._lock = RLock()
        connection = sqlite3.connect(str(db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        self._conn = SerializedConnection(connection, self._lock)
        self._init_schema()
        self._backfill_library_items()
        self._backfill_split_packs()

    def backup_to(self, path: str | Path) -> None:
        """A consistent copy of the whole database at `path`, taken while
        the app keeps running — SQLite's own online backup, under the
        store's lock so no write lands halfway through it."""
        target = sqlite3.connect(str(path))
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()

    def close(self) -> None:
        self._conn.close()
