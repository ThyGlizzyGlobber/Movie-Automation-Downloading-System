"""A box's own Plex Media Server (truenas/obsidian-box.yaml): finding it,
claiming it for the buyer, and giving it its libraries.

A box ships with Plex unclaimed — it belongs to nobody until the buyer signs
in. When they do, in the setup wizard, Obsidian asks plex.tv for a claim
token on their account (good for a few minutes) and leaves it for the NAS
updater, which restarts Plex with it as PLEX_CLAIM: Plex's own image claims
itself for that account as it starts. Obsidian then finds the server among
the account's own, links it, and creates the Movies and TV Shows libraries
on every drive's folders (app/box/drives.py), adding each new drive's as it
comes.

Plex runs on the host's network; from the backend's container that's the
default gateway. PLEX_LOCAL_URL set (it is on a box, to "gateway") is what
makes Plex part of this install at all.
"""

import os
from pathlib import Path

import requests

from app.box import drives
from app.updater import channel

PLEX_LOCAL_URL = os.environ.get("PLEX_LOCAL_URL") or None
PLEX_PORT = 32400
LIBRARIES = (
    # name, Plex type, agent, scanner, and the kind of drive folder it lists
    ("Movies", "movie", "tv.plex.agents.movie", "Plex Movie", "movie"),
    ("TV Shows", "show", "tv.plex.agents.series", "Plex TV Series", "tv"),
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
    return channel.updater_dir() / "plex-claim-request.json"


def claim_state_path() -> Path:
    return channel.updater_dir() / "plex-claim-state.json"


def request_claim(claim_token: str) -> None:
    channel.write_json(claim_request_path(), {"claim_token": claim_token, "requested_at": channel.now_iso()})
    os.chmod(claim_request_path(), 0o600)


def claim_state() -> dict:
    return channel.read_json(claim_state_path()) or {}


def wanted_folders() -> dict[str, list[str]]:
    """Every drive's Movies and TV Shows folder, as Plex sees them: Plex
    mounts each drive at the same /media/<drive> as the backend."""
    return {kind: [str(root) for root in drives.library_roots(kind)] for kind in ("movie", "tv")}


def dropped_folders(settings: dict) -> list[str]:
    """The failed drives' library folders Plex is to stop listing
    (app/box/drive_recovery.py)."""
    folders = settings.get("box_plex_drop")
    return [f for f in folders if isinstance(f, str)] if isinstance(folders, list) else []


def sync_libraries(url: str, token: str, folders: dict[str, list[str]] | None = None,
                   session: requests.Session | None = None, drop: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The box's Movies and TV Shows libraries, listing every drive's
    folder: made when missing, and given any drive's folder they don't
    have yet. Folders already there stay, a drive's that has gone
    missing included (it may come back) and any a person added — except
    those in `drop`, a failed drive's that its titles are being
    downloaded again from (app/box/drive_recovery.py): while Plex still lists
    them it would answer "already on Plex" for every one. A library is
    never left with no folder at all, which Plex refuses; that waits for
    the next drive. A kind with no drive yet is left until there is one:
    Plex won't make a library with no folder. Returns what changed, for
    the log."""
    folders = folders if folders is not None else wanted_folders()
    http = session or requests.Session()
    headers = {"Accept": "application/json", **({"X-Plex-Token": token} if token else {})}
    response = http.get(f"{url}/library/sections", headers=headers, timeout=10)
    response.raise_for_status()
    sections = response.json().get("MediaContainer", {}).get("Directory", []) or []
    changes = []
    for name, kind, agent, scanner, folder_kind in LIBRARIES:
        want = folders.get(folder_kind) or []
        if not want:
            continue
        ours = next((x for x in sections if x.get("type") == kind and x.get("title") == name), None) or next(
            (x for x in sections if x.get("type") == kind
             and any(loc.get("path") in want for loc in x.get("Location") or [])), None)
        if ours is None:
            params = [("name", name), ("type", kind), ("agent", agent), ("scanner", scanner), ("language", "en-US")]
            created = http.post(f"{url}/library/sections", headers=headers,
                                params=params + [("location", f) for f in want], timeout=15)
            created.raise_for_status()
            changes.append(f"made {name}")
            continue
        have = [loc.get("path") for loc in ours.get("Location") or [] if loc.get("path")]
        keep = [f for f in have if f not in drop or f in want]
        missing = [f for f in want if f not in keep]
        if not missing and keep == have:
            continue
        # Plex replaces a library's folders with the list it's sent, so it
        # gets them all; the agent keeps its scanner and settings.
        params = [("agent", ours.get("agent") or agent), ("name", ours.get("title") or name),
                  ("language", ours.get("language") or "en-US")]
        edited = http.put(f"{url}/library/sections/{ours['key']}", headers=headers,
                          params=params + [("location", f) for f in keep + missing], timeout=15)
        edited.raise_for_status()
        said = [f"added {', '.join(missing)}"] if missing else []
        said += [f"removed {', '.join(f for f in have if f not in keep)}"] if keep != have else []
        changes.append(f"{name}: {'; '.join(said)}")
    return changes
