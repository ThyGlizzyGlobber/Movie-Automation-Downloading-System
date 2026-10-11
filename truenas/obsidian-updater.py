#!/usr/bin/env python3
"""Obsidian's updater. Runs on the TrueNAS box itself, as root, every minute
from a TrueNAS cron job, and is the only thing that changes which version of
Obsidian the box runs.

Settings › Updates in the app copies the database and leaves a request in
the app's data folder: a version number and the name of that copy. On its
next run this picks the request up and, in one go:

  1. moves the app's Obsidian images to that exact version (TrueNAS's own
     app.update, which pulls them and restarts the app)
  2. waits for the new version to answer as itself, with the site serving
  3. if it doesn't, puts the previous images back and leaves a note asking
     the old version to restore the database copy as it starts

It talks to TrueNAS through its local socket, as `midclt` does, so it needs
no user and no API key, and the app never holds one. Each step is written to
`updater/state.json` before it's taken, for the Updates page and for a run
that picks up after a reboot mid-update. Every run also leaves a heartbeat,
which is how the app knows updates are set up on this box at all.

The app's data folder is written by the app, and this runs as root, so it is
treated as untrusted: nothing there is followed through a symlink, the
request is a version number checked here, and this script must live where
the app can't write (`install` refuses anywhere else).

Usage, in the TrueNAS shell:

    python3 obsidian-updater.py install --app <app name>    # once per box
    python3 obsidian-updater.py status  --app <app name>
    python3 obsidian-updater.py run     --app <app name>    # what cron runs

Standard library only, so it runs on TrueNAS's own Python with nothing added.
"""

import argparse
import fcntl
import json
import os
import re
import secrets
import stat
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_VERSION = 2
IMAGE_NAMES = ("obsidian-backend", "obsidian-frontend")
IMAGE_PREFIX = "ghcr.io/thyglizzyglobber/"
CRON_DESCRIPTION = "Obsidian updater (installs updates requested in the app)"
# How long a new release gets to answer as itself, and the old one after a
# rollback, before giving up on it.
VERIFY_SECONDS = 300
MAX_FILE_BYTES = 64 * 1024
LOG_BYTES = 1024 * 1024

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_BACKUP = re.compile(r"^obsidian-[A-Za-z0-9.-]+\.db$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Log:
    def __init__(self, path: Path | None, echo: bool = False):
        self.path, self.echo = path, echo

    def __call__(self, message: str) -> None:
        line = f"{now_iso()} {message}"
        if self.echo:
            print(line)
        if self.path is None:
            return
        try:
            if self.path.exists() and self.path.stat().st_size > LOG_BYTES:
                self.path.replace(self.path.with_suffix(".log.1"))
            with self.path.open("a") as log:
                log.write(line + "\n")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# TrueNAS
# ---------------------------------------------------------------------------


class Middleware:
    """TrueNAS's API over its local socket, as root."""

    def _client(self):
        try:
            from truenas_api_client import Client
        except ImportError:  # older releases named it differently
            from middlewared.client import Client
        return Client()

    def app_config(self, app: str) -> dict:
        with self._client() as c:
            return c.call("app.config", app)

    def app_update(self, app: str, compose: dict) -> None:
        """Waits for TrueNAS to finish; raises if the job fails."""
        with self._client() as c:
            c.call("app.update", app, {"custom_compose_config": compose}, job=True)

    def cron_jobs(self, description: str) -> list[dict]:
        with self._client() as c:
            return c.call("cronjob.query", [["description", "=", description]])

    def cron_save(self, job_id: int | None, data: dict) -> None:
        with self._client() as c:
            if job_id is None:
                c.call("cronjob.create", data)
            else:
                c.call("cronjob.update", job_id, data)


# ---------------------------------------------------------------------------
# The app's compose config
# ---------------------------------------------------------------------------


def our_image(ref: str, prefix: str) -> str | None:
    """"obsidian-backend" for one of our images at any tag; else None. Only
    these are ever retagged; the rest of the app is left as written."""
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return None
    name = ref[len(prefix):].split("@", 1)[0].split(":", 1)[0]
    return name if name in IMAGE_NAMES else None


def retag(compose: dict, tag: str, prefix: str) -> tuple[dict, dict[str, str]]:
    moved = json.loads(json.dumps(compose))
    before: dict[str, str] = {}
    for name, service in (moved.get("services") or {}).items():
        image = (service or {}).get("image")
        ours = our_image(image, prefix)
        if ours:
            before[name] = image
            service["image"] = f"{prefix}{ours}:{tag}"
    return moved, before


def restore_images(compose: dict, images: dict[str, str]) -> dict:
    moved = json.loads(json.dumps(compose))
    for name, image in images.items():
        if name in (moved.get("services") or {}):
            moved["services"][name]["image"] = image
    return moved


def _service(compose: dict, wanted: str, prefix: str) -> dict:
    """The service running one of our images: by image on a release install,
    by name before one (a git-pull install builds its images instead)."""
    services = compose.get("services") or {}
    for service in services.values():
        if our_image((service or {}).get("image"), prefix) == f"obsidian-{wanted}":
            return service
    return services.get(wanted) or {}


def data_dir(compose: dict, prefix: str) -> str:
    """The host folder mounted as the backend's /app/data."""
    for volume in _service(compose, "backend", prefix).get("volumes") or []:
        if isinstance(volume, str):
            parts = volume.split(":")
            if len(parts) >= 2 and parts[1] == "/app/data":
                source = parts[0]
                break
        elif isinstance(volume, dict) and volume.get("target") == "/app/data":
            source = volume.get("source")
            break
    else:
        raise RuntimeError("couldn't find the backend's /app/data mount in the app")
    if not source or not source.startswith("/"):
        raise RuntimeError("the backend's /app/data has to be a folder on the NAS, not a named volume")
    return source


def site_url(compose: dict, prefix: str) -> str:
    """The site as the NAS itself reaches it: the frontend's published port."""
    for port in _service(compose, "frontend", prefix).get("ports") or []:
        if isinstance(port, dict):
            if str(port.get("target")) == "80" and port.get("published"):
                return f"http://127.0.0.1:{port['published']}"
        else:
            parts = str(port).split("/", 1)[0].split(":")
            if len(parts) >= 2 and parts[-1] == "80":
                return f"http://127.0.0.1:{parts[-2]}"
    raise RuntimeError("couldn't find the frontend's published port in the app")


def bind_sources(compose: dict) -> list[str]:
    sources = []
    for service in (compose.get("services") or {}).values():
        for volume in (service or {}).get("volumes") or []:
            source = volume.split(":", 1)[0] if isinstance(volume, str) else (volume or {}).get("source")
            if isinstance(source, str) and source.startswith("/"):
                sources.append(os.path.realpath(source))
    return sources


# ---------------------------------------------------------------------------
# The app's data folder, which the app can write: never followed through a
# symlink, files read capped and written whole.
# ---------------------------------------------------------------------------


class DataFolder:
    def __init__(self, path: str):
        root = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                os.mkdir("updater", 0o755, dir_fd=root)
            except FileExistsError:
                pass
            self.fd = os.open("updater", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        finally:
            os.close(root)

    def close(self) -> None:
        os.close(self.fd)

    def read(self, name: str) -> dict | None:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def write(self, name: str, value: dict) -> None:
        temporary = f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=self.fd)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
        os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def remove(self, name: str) -> None:
        try:
            os.unlink(name, dir_fd=self.fd)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------


def _get(url: str, timeout: float = 5) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read(MAX_FILE_BYTES)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (OSError, ValueError):
        return 0, b""


class Updater:
    def __init__(self, middleware, app: str, *, prefix: str = IMAGE_PREFIX, url: str | None = None,
                 verify_seconds: int = VERIFY_SECONDS, sleep=time.sleep, log=None):
        self.mw = middleware
        self.app = app
        self.prefix = prefix
        self.url = url
        self.verify_seconds = verify_seconds
        self.sleep = sleep
        self.log = log or Log(None)
        self.folder: DataFolder | None = None

    # -- the site --

    def running_version(self) -> str | None:
        status, body = _get(f"{self.base}/api/health")
        if status != 200:
            return None
        try:
            return json.loads(body).get("version")
        except (ValueError, AttributeError):
            return None

    def site_up(self) -> bool:
        return _get(f"{self.base}/")[0] == 200

    def wait_for(self, wanted: str | None) -> bool:
        deadline = time.monotonic() + self.verify_seconds
        while True:
            if wanted and self.running_version() == wanted and self.site_up():
                return True
            if time.monotonic() >= deadline:
                return False
            self.beat()
            self.sleep(3)

    # -- state --

    def beat(self) -> None:
        self.folder.write("heartbeat.json", {"at": now_iso(), "script": SCRIPT_VERSION})

    def state(self) -> dict:
        return self.folder.read("state.json") or {"state": "idle"}

    def write_state(self, **changes) -> dict:
        state = {**self.state(), **changes, "updated_at": now_iso()}
        self.folder.write("state.json", state)
        return state

    def finish(self, outcome: str, message: str) -> None:
        self.log(f"{outcome}: {message}")
        self.write_state(state=outcome, message=message, finished_at=now_iso())

    # -- the run --

    def run(self) -> None:
        compose = self.mw.app_config(self.app)
        self.base = self.url or site_url(compose, self.prefix)
        self.folder = DataFolder(data_dir(compose, self.prefix))
        try:
            self.beat()
            request = self.folder.read("request.json")
            state = self.state()
            if request is not None:
                self.folder.remove("request.json")  # claimed: never applied twice
                self.update(request, compose)
            elif state.get("state") in ("updating", "verifying", "rolling_back"):
                self.recover(state)
            elif state.get("state") == "queued":
                self.finish("failed", "The update request went missing before it started. Try again.")
        finally:
            self.folder.close()

    def update(self, request: dict, compose: dict) -> None:
        target = request.get("version")
        if not isinstance(target, str) or not _VERSION.match(target):
            self.finish("failed", "The update request didn't name a version.")
            return
        backup = request.get("backup")
        if not isinstance(backup, str) or not _BACKUP.match(backup):
            backup = None
        current = self.running_version()
        self.log(f"updating {self.app} from {current} to {target}")
        self.write_state(
            state="updating", from_version=current, to_version=target, backup=backup,
            started_at=now_iso(), finished_at=None, message=None, previous_images=None,
        )
        moved, before = retag(compose, target, self.prefix)
        if not before:
            self.finish("failed", f"The TrueNAS app {self.app!r} doesn't run Obsidian's release images.")
            return
        self.write_state(previous_images=before)
        try:
            self.mw.app_update(self.app, moved)
        except Exception as exc:  # noqa: BLE001 — whatever TrueNAS raised, it's the reason
            self.after_failed_update(_reason(exc))
            return
        self.verify()

    def verify(self) -> None:
        target = self.write_state(state="verifying")["to_version"]
        if self.wait_for(target):
            self.finish("done", f"Updated to {target}.")
        else:
            minutes = max(1, round(self.verify_seconds / 60))
            self.roll_back(f"Version {target} didn't start properly within {minutes} minute{'' if minutes == 1 else 's'}")

    def after_failed_update(self, reason: str) -> None:
        """TrueNAS couldn't finish. If the old version is still up, nothing
        changed and there's nothing to undo; otherwise put it back."""
        previous = self.state().get("from_version")
        if previous and self.running_version() == previous and self.site_up():
            self.finish("failed", f"The update couldn't be installed, so nothing changed: {reason}")
        else:
            self.roll_back(f"The update couldn't be installed ({reason})")

    def roll_back(self, reason: str) -> None:
        state = self.state()
        previous_images, previous = state.get("previous_images"), state.get("from_version")
        if not previous_images:
            self.finish("failed", f"{reason}, and there was nothing recorded to go back to.")
            return
        self.log(f"rolling back to {previous}: {reason}")
        self.write_state(state="rolling_back", message=reason)
        # Before anything moves: the old backend reads this as it starts.
        # The version in it is what keeps the new one from acting on it.
        if state.get("backup") and previous:
            self.folder.write("restore.json", {"backup": state["backup"], "version": previous})
        try:
            compose = self.mw.app_config(self.app)
            self.mw.app_update(self.app, restore_images(compose, previous_images))
        except Exception as exc:  # noqa: BLE001
            self.finish("failed", f"{reason}, and going back to {previous} failed too: {_reason(exc)}")
            return
        if self.wait_for(previous):
            self.finish("rolled_back", f"{reason}, so Obsidian went back to {previous}.")
        else:
            self.finish("failed", f"{reason}. Going back to {previous} didn't come up either.")

    def recover(self, state: dict) -> None:
        """A run that ended mid-update — the NAS restarted, say."""
        self.log(f"picking up an update left {state.get('state')}")
        if state.get("state") == "verifying":
            self.verify()
        elif state.get("state") == "updating":
            running = self.running_version()
            if running == state.get("to_version") and self.site_up():
                self.verify()
            elif running and running == state.get("from_version") and self.site_up():
                self.finish("failed", "The update was interrupted before it started, so nothing changed. Try again.")
            else:
                self.roll_back("The update was interrupted")
        else:
            self.roll_back(state.get("message") or "The update didn't start properly")


def _reason(exc: Exception) -> str:
    return str(getattr(exc, "error", None) or exc).strip() or type(exc).__name__


# ---------------------------------------------------------------------------
# Installing it
# ---------------------------------------------------------------------------


def _owned_by_root_only(path: Path) -> bool:
    info = path.stat()
    return info.st_uid == 0 and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def install(middleware, app: str, script: Path, prefix: str = IMAGE_PREFIX) -> list[str]:
    """Checks this script is somewhere only root can change and the app
    can't reach, then creates (or refreshes) the cron job that runs it."""
    script = script.resolve()
    folder = script.parent
    if os.geteuid() != 0:
        raise SystemExit("Run this as root, in the TrueNAS shell.")
    compose = middleware.app_config(app)
    # The script is ours to lock down; its folder is only checked, never
    # changed — it could be anything, up to a whole pool.
    os.chmod(script, 0o700)
    for path in (folder, script):
        if not _owned_by_root_only(path):
            raise SystemExit(
                f"{path} has to belong to root and be writable only by root. Give the script a folder "
                "of its own: install -d -m 700 <folder>"
            )
    for source in bind_sources(compose):
        inside = os.path.commonpath([str(folder), source]) in (str(folder), source)
        if inside:
            raise SystemExit(
                f"{folder} overlaps {source}, which the app mounts. Put this script somewhere the "
                "app can't write, such as a folder of its own on a pool."
            )
    notes = []
    if not any(our_image((s or {}).get("image"), prefix) for s in (compose.get("services") or {}).values()):
        notes.append(f"The app {app!r} doesn't run Obsidian's release images yet; updates start working once it does.")
    data = {
        "user": "root",
        "command": f"/usr/bin/python3 {script} run --app {app}",
        "description": CRON_DESCRIPTION,
        "schedule": {"minute": "*", "hour": "*", "dom": "*", "month": "*", "dow": "*"},
        "stdout": True,
        "stderr": True,
        "enabled": True,
    }
    existing = middleware.cron_jobs(CRON_DESCRIPTION)
    middleware.cron_save(existing[0]["id"] if existing else None, data)
    notes.append(f"Cron job {'updated' if existing else 'created'}: runs every minute as root.")
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Installs Obsidian updates requested in the app.")
    parser.add_argument("command", choices=("run", "install", "status"))
    parser.add_argument("--app", required=True, help="the app's name in TrueNAS (Apps › Installed)")
    parser.add_argument("--image-prefix", default=IMAGE_PREFIX, help=argparse.SUPPRESS)
    parser.add_argument("--url", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    script = Path(__file__)
    log = Log(script.resolve().parent / "updater.log", echo=args.command != "run")
    middleware = Middleware()

    if args.command == "install":
        for note in install(middleware, args.app, script, args.image_prefix):
            print(note)
        # One run now, so a problem shows here rather than in a log a minute
        # from now, and the app sees the heartbeat straight away.
        if run_once(middleware, args, script, log) != 0:
            print(f"The first run failed; see {script.resolve().parent / 'updater.log'}.")
            return 1
        print("First run done.")
        args.command = "status"

    if args.command == "status":
        compose = middleware.app_config(args.app)
        folder = DataFolder(data_dir(compose, args.image_prefix))
        try:
            print(json.dumps({"state": folder.read("state.json"), "heartbeat": folder.read("heartbeat.json")}, indent=2))
        finally:
            folder.close()
        return 0

    return run_once(middleware, args, script, log)


def run_once(middleware, args, script: Path, log) -> int:
    lock = open(script.resolve().parent / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # the previous run is still busy with an update
    try:
        Updater(middleware, args.app, prefix=args.image_prefix, url=args.url, log=log).run()
    except Exception as exc:  # noqa: BLE001 — logged for whoever looks; cron stays quiet
        log(f"run failed: {_reason(exc)}")
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
