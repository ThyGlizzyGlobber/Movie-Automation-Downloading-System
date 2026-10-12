"""How the app talks to the NAS updater (truenas/obsidian-updater.py), which
runs as root on the host every minute: JSON files in the data folder's
`updater/`, written whole and read forgivingly, and the heartbeat that says
the updater is there at all.

The container the internet reaches holds no TrueNAS credential, so anything
that changes the host — an update, the box's VPN, claiming its Plex,
adding a drive — is a request file left here for the updater to apply. Each
feature names its own files (updates.py, box/vpn.py, box/plex.py); this is
what they share."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.core import config

# The updater runs every minute and leaves a heartbeat each time. Older
# than this, it isn't running — or was never set up on this box.
HEARTBEAT_SECONDS = 180


def data_dir() -> Path:
    return Path(config.DB_PATH).parent


def updater_dir() -> Path:
    return data_dir() / "updater"


def heartbeat_path() -> Path:
    return updater_dir() / "heartbeat.json"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_json(path: Path, value: dict) -> None:
    """Whole or not at all: written beside, then renamed over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2))
    os.replace(temporary, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def updater_running() -> bool:
    """Whether the updater has run in the last few minutes."""
    beat = read_json(heartbeat_path()) or {}
    try:
        at = datetime.fromisoformat(beat["at"])
    except (KeyError, TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - at).total_seconds() < HEARTBEAT_SECONDS
