"""Where a box is on its home network, for people to bookmark.

A box is always obsidian.local, which follows it whatever address the
router gives it. A few devices — some Android phones among them — can't
open .local names, so the app also shows the address itself, as the NAS
updater last saw it (truenas/obsidian-updater.py writes network.json every
minute). The box keeps getting its address from the router rather than
fixing one (truenas/provision.py): routers hand a device back the same
address, and a fixed one breaks the day the router is replaced.

Only on a box: its web page is on port 80, so the address alone opens it.
"""

from datetime import datetime, timezone

from app.box import drives
from app.updater import channel

# The updater writes it every minute; much older, and the box may have
# moved since.
FRESH_SECONDS = 15 * 60


def addresses() -> list[str]:
    """The box's web addresses, its name first; empty off a box."""
    if not drives.managed():
        return []
    report = channel.read_json(channel.updater_dir() / "network.json") or {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(report["at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = None
    found = []
    hostname = report.get("hostname")
    if report.get("mdns") and isinstance(hostname, str) and hostname.isascii() and hostname.replace("-", "").isalnum():
        found.append(f"http://{hostname}.local")
    if age is not None and age < FRESH_SECONDS:
        found += [f"http://{a}" for a in report.get("addresses") or [] if isinstance(a, str) and _is_ipv4(a)]
    return found


def _is_ipv4(text: str) -> bool:
    parts = text.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
