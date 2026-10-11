"""A box's own Plex Media Server (truenas/obsidian-box.yaml): finding it,
claiming it for the buyer, and giving it its libraries.

A box ships with Plex unclaimed — it belongs to nobody until the buyer signs
in. When they do, in the setup wizard, Obsidian asks plex.tv for a claim
token on their account (good for a few minutes) and leaves it for the NAS
updater, which restarts Plex with it as PLEX_CLAIM: Plex's own image claims
itself for that account as it starts. Obsidian then finds the server among
the account's own, links it, and creates the Movies and TV Shows libraries
on the box's media folders.

Plex runs on the host's network; from the backend's container that's the
default gateway. PLEX_LOCAL_URL set (it is on a box, to "gateway") is what
makes Plex part of this install at all.
"""

import os
from pathlib import Path

import requests

from app import updates

PLEX_LOCAL_URL = os.environ.get("PLEX_LOCAL_URL") or None
PLEX_PORT = 32400
LIBRARIES = (
    # name, Plex type, agent, scanner, the folder on the box's media dataset
    ("Movies", "movie", "tv.plex.agents.movie", "Plex Movie", "/media/Movies"),
    ("TV Shows", "show", "tv.plex.agents.series", "Plex TV Series", "/media/TV Shows"),
)


def managed() -> bool:
    return bool(PLEX_LOCAL_URL)


def default_gateway(route_table: Path = Path("/proc/net/route")) -> str | None:
    """The container's default gateway, little-endian hex in the kernel's
    route table — the box itself, on the app's bridge network."""
    try:
        lines = route_table.read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000" and fields[2] != "00000000":
            return ".".join(str(b) for b in reversed(bytes.fromhex(fields[2])))
    return None


def local_url() -> str | None:
    if PLEX_LOCAL_URL == "gateway":
        gateway = default_gateway()
        return f"http://{gateway}:{PLEX_PORT}" if gateway else None
    return PLEX_LOCAL_URL


def identity(url: str) -> dict | None:
    """The server's machine id and whether it's claimed. Plex answers this
    one without a token, claimed or not."""
    try:
        response = requests.get(f"{url}/identity", headers={"Accept": "application/json"}, timeout=5)
        container = response.json().get("MediaContainer", {}) if response.ok else None
    except (requests.RequestException, ValueError):
        return None
    if not container or not container.get("machineIdentifier"):
        return None
    return {"machine_identifier": container["machineIdentifier"], "claimed": str(container.get("claimed")) in ("1", "true", "True")}


def claim_request_path() -> Path:
    return updates.updater_dir() / "plex-claim-request.json"


def claim_state_path() -> Path:
    return updates.updater_dir() / "plex-claim-state.json"


def request_claim(claim_token: str) -> None:
    updates.write_json(claim_request_path(), {"claim_token": claim_token, "requested_at": updates.now_iso()})
    os.chmod(claim_request_path(), 0o600)


def claim_state() -> dict:
    return updates.read_json(claim_state_path()) or {}


def ensure_libraries(url: str, token: str, session: requests.Session | None = None) -> list[str]:
    """Creates whichever of the box's two libraries the server doesn't
    already have a library on that folder for. Returns the names made."""
    http = session or requests.Session()
    headers = {"Accept": "application/json", "X-Plex-Token": token}
    response = http.get(f"{url}/library/sections", headers=headers, timeout=10)
    response.raise_for_status()
    taken = {
        location.get("path")
        for section in response.json().get("MediaContainer", {}).get("Directory", []) or []
        for location in section.get("Location", []) or []
    }
    made = []
    for name, kind, agent, scanner, folder in LIBRARIES:
        if folder in taken:
            continue
        created = http.post(
            f"{url}/library/sections",
            headers=headers,
            params={"name": name, "type": kind, "agent": agent, "scanner": scanner, "language": "en-US", "location": folder},
            timeout=15,
        )
        created.raise_for_status()
        made.append(name)
    return made
