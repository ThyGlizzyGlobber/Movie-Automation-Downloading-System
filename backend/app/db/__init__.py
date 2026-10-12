"""SQLite job store — request state survives backend restarts. Plain
sqlite3, no ORM (household scale, per the plan's confirmed architecture).
A single connection with `check_same_thread=False` guarded by a
`threading.Lock`; callers on the async side wrap calls in
`asyncio.to_thread` so a query never blocks the event loop.

Callers import from here, never from the modules inside: `RequestStore`
(store.py), the row types (rows.py) and the status groups (common.py)."""

from app.db.common import FAILED_OR_CANCELLED_STATUSES, FAILURE_STATUSES, NON_TERMINAL_STATUSES
from app.db.rows import PlexServerRow, RequestRow, SessionRow, ShowEpisodeRow, ShowRow, UserRow
from app.db.store import RequestStore

__all__ = [
    "FAILED_OR_CANCELLED_STATUSES",
    "FAILURE_STATUSES",
    "NON_TERMINAL_STATUSES",
    "PlexServerRow",
    "RequestRow",
    "RequestStore",
    "SessionRow",
    "ShowEpisodeRow",
    "ShowRow",
    "UserRow",
]
