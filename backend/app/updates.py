"""In-app updates for a release install: what's newer, what changed, and
the database copy taken before anything moves.

A release install runs the images .github/workflows/release.yml publishes,
and updating it means pointing the TrueNAS app at a newer tag of them. That
last step needs a TrueNAS API key, which lives only in the updater sidecar
(app/updater.py) — never in this backend, the container the internet
reaches. Between them:

  backend   asks the registry what's newest on this box's channel, shows the
            release notes, copies the database, then asks the updater to go
  updater   moves the app's images to that exact version through TrueNAS,
            then checks the new release came up; if it didn't, it moves them
            back and leaves a note asking the old backend to restore the
            database copy as it boots (`restore_pending_backup`)

The two share nothing but files under the app's data volume (UPDATER_DIR):
the updater's state, the token the backend proves itself with, and that
restore note. Every file here is written whole-or-not-at-all, since either
container can be replaced mid-update.
"""

import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from app import config, version

logger = logging.getLogger(__name__)

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """"1.2.3" -> (1, 2, 3). Anything else, a channel name included, is None."""
    match = _VERSION.match(value or "")
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def is_newer(candidate: str | None, current: str | None) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return bool(new and old and new > old)


def channel() -> str | None:
    """The channel this box follows; None on a git-pull install."""
    return config.UPDATE_CHANNEL or version.current().channel


def image_ref(image: str, tag: str) -> str:
    return f"{config.IMAGE_REGISTRY}/{config.IMAGE_OWNER}/{image}:{tag}"


def is_our_image(ref: str) -> str | None:
    """The image name ("obsidian-backend") when `ref` is one of ours, at any
    tag. Only these are ever retagged; anything else in the app is left as
    the person installing it wrote it."""
    prefix = f"{config.IMAGE_REGISTRY}/{config.IMAGE_OWNER}/"
    if not ref.startswith(prefix):
        return None
    name = ref[len(prefix):].split(":", 1)[0].split("@", 1)[0]
    return name if name in config.IMAGE_NAMES else None


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


class RegistryError(Exception):
    """The registry couldn't be asked, as opposed to answering "no"."""


_MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
_TIMEOUT = 15


class _Registry:
    """The standard registry API, anonymously: GHCR hands a pull token to
    anyone for a public image, and a plain local registry needs none. A 401
    names where to get the token (`WWW-Authenticate: Bearer realm=...`)."""

    def __init__(self, image: str):
        self.repo = f"{config.IMAGE_OWNER}/{image}"
        self.http = requests.Session()

    def get(self, path: str, accept: str = _MANIFEST_TYPES) -> requests.Response:
        url = f"{config.IMAGE_REGISTRY_URL}/v2/{self.repo}/{path}"
        try:
            response = self.http.get(url, headers={"Accept": accept}, timeout=_TIMEOUT)
            if response.status_code == 401 and "Authorization" not in self.http.headers:
                self.http.headers["Authorization"] = f"Bearer {self._token(response)}"
                response = self.http.get(url, headers={"Accept": accept}, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RegistryError(f"couldn't reach the image registry: {exc}") from exc
        return response

    def _token(self, challenge: requests.Response) -> str:
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge.headers.get("WWW-Authenticate", "")))
        realm = params.pop("realm", None)
        if not realm:
            raise RegistryError("the image registry asked for credentials")
        try:
            answer = self.http.get(realm, params=params, timeout=_TIMEOUT)
            answer.raise_for_status()
            return answer.json()["token"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise RegistryError(f"couldn't get a pull token: {exc}") from exc

    def manifest(self, reference: str) -> dict | None:
        response = self.get(f"manifests/{reference}")
        if response.status_code == 404:
            return None
        if not response.ok:
            raise RegistryError(f"the image registry answered {response.status_code}")
        return response.json()


def published_version(tag: str, image: str = "obsidian-backend") -> str | None:
    """The version baked into `image:tag` (its org.opencontainers.image.version
    label), or None if there's no such tag. For a channel tag, that's what an
    update would install."""
    registry = _Registry(image)
    manifest = registry.manifest(tag)
    if manifest is None:
        return None
    if "manifests" in manifest:  # an index: pick the x86-64 image out of it
        entry = next(
            (m for m in manifest["manifests"]
             if m.get("platform", {}).get("os") == "linux" and m.get("platform", {}).get("architecture") == "amd64"),
            None,
        )
        if entry is None:
            raise RegistryError(f"{image}:{tag} has no x86-64 image")
        manifest = registry.manifest(entry["digest"])
        if manifest is None:
            raise RegistryError(f"{image}:{tag} names an image the registry doesn't have")
    blob = registry.get(f"blobs/{manifest['config']['digest']}", accept="application/json")
    if not blob.ok:
        raise RegistryError(f"couldn't read {image}:{tag}'s configuration ({blob.status_code})")
    labels = (blob.json().get("config") or {}).get("Labels") or {}
    return labels.get("org.opencontainers.image.version")


def release_exists(release: str) -> bool:
    """Whether every one of our images is published at this exact version."""
    return all(_Registry(image).manifest(release) is not None for image in config.IMAGE_NAMES)


# ---------------------------------------------------------------------------
# Release notes
# ---------------------------------------------------------------------------


def parse_release_notes(changelog: str, release: str) -> list[dict]:
    """One version's section of CHANGELOG.md as [{"heading", "items"}], in
    plain text: bullets joined across their wrapped lines, bold and code
    marks dropped. Empty when the version has no section of its own."""
    lines = changelog.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(rf"^## \[{re.escape(release)}\]", line)), None)
    if start is None:
        return []
    groups: list[dict] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("### "):
            groups.append({"heading": line[4:].strip(), "items": []})
        elif line.startswith("- "):
            if not groups:
                groups.append({"heading": "Changes", "items": []})
            groups[-1]["items"].append(line[2:].strip())
        elif line.startswith("  ") and line.strip() and groups and groups[-1]["items"]:
            groups[-1]["items"][-1] += " " + line.strip()
    for group in groups:
        group["items"] = [re.sub(r"\*\*|`", "", item) for item in group["items"]]
    return [group for group in groups if group["items"]]


def release_notes(release: str) -> list[dict]:
    """What changed in `release`, read from CHANGELOG.md at its tag. A beta
    build has no tag of its own, so it has no notes; nor does a release
    whose notes can't be fetched right now, which is no reason to block it."""
    url = f"https://raw.githubusercontent.com/{config.SOURCE_REPO}/v{release}/CHANGELOG.md"
    try:
        response = requests.get(url, timeout=_TIMEOUT)
    except requests.RequestException:
        return []
    return parse_release_notes(response.text, release) if response.ok else []


# ---------------------------------------------------------------------------
# Files the backend and the updater share on the data volume
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    return Path(config.DB_PATH).parent


def updater_dir() -> Path:
    return data_dir() / "updater"


def state_path() -> Path:
    return updater_dir() / "state.json"


def restore_path() -> Path:
    return updater_dir() / "restore.json"


def token_path() -> Path:
    return updater_dir() / "token"


def backups_dir() -> Path:
    return data_dir() / "backups"


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


def shared_token(create: bool = False) -> str | None:
    """The secret the backend shows the updater on every call. The updater
    makes it on first start; the backend only ever reads it. Only the two
    containers mounting the data volume can see it."""
    path = token_path()
    try:
        token = path.read_text().strip()
    except OSError:
        token = ""
    if token or not create:
        return token or None
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(token)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return token


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Database copies
# ---------------------------------------------------------------------------


def backup_database(store, from_version: str, to_version: str) -> Path:
    """Copies the live database before an update, and keeps the newest
    UPDATE_BACKUPS_KEPT. Named for the move it was taken for, so a person
    looking in the folder can tell them apart."""
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"obsidian-{stamp}-{from_version}-to-{to_version}.db"
    store.backup_to(path)
    for old in sorted(folder.glob("obsidian-*.db"), reverse=True)[config.UPDATE_BACKUPS_KEPT:]:
        old.unlink(missing_ok=True)
    return path


def restore_pending_backup(db_path: Path, running_version: str | None) -> str | None:
    """Run before the store opens. If the updater rolled back to this very
    version and left a note to restore the copy taken before the failed
    update, put that copy back and return its name.

    The version check is what makes this safe: a note meant for the old
    version is ignored by the new one, which may still be running (and
    restarting) for the moments before the rollback replaces it."""
    note = read_json(restore_path())
    if not note or not running_version or note.get("version") != running_version:
        return None
    backup = backups_dir() / str(note.get("backup", ""))
    if not note.get("backup") or not backup.is_file():
        logger.error("a rollback asked for database copy %r, which isn't there; keeping the current one", note.get("backup"))
        restore_path().unlink(missing_ok=True)
        return None
    # Checked before it replaces anything: a copy that won't open is worse
    # than the database already here.
    try:
        check = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        check.execute("SELECT count(*) FROM sqlite_master").fetchone()
        check.close()
    except sqlite3.Error as exc:
        logger.error("database copy %s won't open (%s); keeping the current one", backup.name, exc)
        restore_path().unlink(missing_ok=True)
        return None
    db_path = Path(db_path)
    if db_path.exists():
        shutil.copy2(db_path, db_path.with_name(f"{db_path.name}.before-restore-{int(time.time())}"))
    temporary = db_path.with_name(f".{db_path.name}.restoring")
    shutil.copy2(backup, temporary)
    os.replace(temporary, db_path)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    restore_path().unlink(missing_ok=True)
    return backup.name
