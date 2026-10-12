"""What every part of the store shares: the status groups callers filter
by, and the timestamp format every row is written with."""

from datetime import datetime, timezone

# Rows in these statuses are still live — an active job, or a torrent the
# download watcher is still tracking. Retention purges (automatic or the
# "Clear My Requests" button) never touch them, only settled history.
NON_TERMINAL_STATUSES = {"queued", "searching", "downloading"}

# Settled without a filed download, and not by choice — what the episode
# auto-recheck retries and /api/admin/jobs lists by default.
FAILURE_STATUSES = frozenset({"failed", "no qualifying results", "insufficient free space", "downloaded, not filed"})

# The same, plus a deliberate "cancelled": every way a request can end up
# settled without the episode, which a season view shows as failed.
FAILED_OR_CANCELLED_STATUSES = FAILURE_STATUSES | {"cancelled"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
