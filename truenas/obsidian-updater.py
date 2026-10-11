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

On a box (truenas/obsidian-box.yaml) it also applies the buyer's VPN
details, entered in the app and left as `updater/vpn-request.json`: checked
again here, written into the gluetun service's settings, and the request
deleted, so the secrets end up only in TrueNAS's root-only app config.
Likewise a Plex claim token (`updater/plex-claim-request.json`), left when
the buyer first signs in to Plex: written into the plex service as
PLEX_CLAIM, so the box's new Plex server claims itself for their account.

And a box's drives. Every run it reports, in `updater/storage.json`, the
unused disks it can see and how each of the box's drives is doing; a
request to add one (`updater/drive-request.json`, from Settings › Storage)
names a disk by name and serial, which must still be unused. The disk gets
a pool of its own (drive1, drive2, …), laid out alike, marked, and mounted
into Obsidian, qBittorrent and Plex at /media/<drive>. Its data is erased:
the app has the person confirm that first.

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
import base64
import binascii
import fcntl
import ipaddress
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

SCRIPT_VERSION = 6
IMAGE_NAMES = ("obsidian-backend", "obsidian-frontend")
IMAGE_PREFIX = "ghcr.io/thyglizzyglobber/"
CRON_DESCRIPTION = "Obsidian updater (installs updates requested in the app)"
# How long a new release gets to answer as itself, and the old one after a
# rollback, before giving up on it.
VERIFY_SECONDS = 300
MAX_FILE_BYTES = 64 * 1024
LOG_BYTES = 1024 * 1024

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

# The VPN providers a box can use and what each connection needs: the same
# table as the app's (backend/app/vpn.py), checked against again here, since
# this runs as root and the request comes from a folder the app can write.
VPN_PROVIDERS = {
    "mullvad": {"wireguard": ["private_key", "addresses"]},
    "protonvpn": {"wireguard": ["private_key"], "openvpn": ["user", "password"]},
    "nordvpn": {"openvpn": ["user", "password"], "wireguard": ["private_key"]},
    "surfshark": {"wireguard": ["private_key", "addresses"], "openvpn": ["user", "password"]},
    "private internet access": {"openvpn": ["user", "password"]},
    "ivpn": {"wireguard": ["private_key", "addresses"]},
    "windscribe": {"wireguard": ["private_key", "addresses", "preshared_key"]},
    "airvpn": {"wireguard": ["private_key", "addresses", "preshared_key"]},
    "expressvpn": {"openvpn": ["user", "password"]},
    "custom": {"wireguard": ["private_key", "addresses", "public_key", "endpoint_ip", "endpoint_port"]},
}
VPN_OPTIONAL = {"preshared_key", "server_countries"}
VPN_ENV = {
    "private_key": "WIREGUARD_PRIVATE_KEY",
    "public_key": "WIREGUARD_PUBLIC_KEY",
    "preshared_key": "WIREGUARD_PRESHARED_KEY",
    "addresses": "WIREGUARD_ADDRESSES",
    "endpoint_ip": "WIREGUARD_ENDPOINT_IP",
    "endpoint_port": "WIREGUARD_ENDPOINT_PORT",
    "user": "OPENVPN_USER",
    "password": "OPENVPN_PASSWORD",
    "server_countries": "SERVER_COUNTRIES",
}
# Every gluetun setting this manages; the rest of gluetun's are left alone.
VPN_MANAGED = set(VPN_ENV.values()) | {"VPN_SERVICE_PROVIDER", "VPN_TYPE"}
GLUETUN_IMAGE = "qmcgaw/gluetun:"
# A box's drives: pools named drive1, drive2, … each mounted at /media/<name>
# in these services, laid out alike, and marked once ready (app/drives.py).
DRIVE_POOL = re.compile(r"^drive(\d+)$")
DRIVE_LAYOUT = ("downloads/movies", "downloads/tv", "Movies", "TV Shows")
DRIVE_MARKER = ".obsidian-drive.json"
QBITTORRENT_IMAGE = "lscr.io/linuxserver/qbittorrent:"
APPS_UID = 568
PLEX_IMAGE = "plexinc/pms-docker:"
_CLAIM_TOKEN = re.compile(r"^claim-[A-Za-z0-9_-]{8,100}$")
_BACKUP = re.compile(r"^obsidian-[A-Za-z0-9.-]+\.db$")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
# Docker's and the apps' own interfaces, which aren't the home network.
VIRTUAL_INTERFACES = ("docker", "br-", "veth", "kube", "cni", "flannel", "vnet", "lo")


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

    def unused_disks(self) -> list[dict]:
        with self._client() as c:
            return c.call("disk.get_unused")

    def pools(self) -> list[dict]:
        with self._client() as c:
            return c.call("pool.query")

    def network(self) -> tuple[dict, dict]:
        """The box's addresses on the home network right now, and its name."""
        with self._client() as c:
            return c.call("network.general.summary"), c.call("network.configuration.config")

    def create_drive_pool(self, name: str, disk: str) -> None:
        """One disk, one pool: a box's drives are separate by design."""
        with self._client() as c:
            c.call("pool.create", {
                "name": name, "encryption": False, "topology": {"data": [{"type": "STRIPE", "disks": [disk]}]},
            }, job=True)

    def give_to_apps(self, path: str) -> None:
        """qBittorrent and Plex run as TrueNAS's apps user (568)."""
        with self._client() as c:
            c.call("filesystem.setperm", {
                "path": path, "uid": APPS_UID, "gid": APPS_UID, "mode": "775",
                "options": {"recursive": True, "stripacl": True},
            }, job=True)

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


def vpn_env(request: dict) -> dict[str, str]:
    """gluetun's settings for a VPN request, or ValueError. Every value is
    checked for its shape: keys are 32 bytes of base64, addresses parse,
    names and passwords have no control characters."""
    provider, kind = request.get("provider"), request.get("type")
    fields = (VPN_PROVIDERS.get(provider) or {}).get(kind)
    if fields is None:
        raise ValueError(f"not a provider and connection this box can use: {provider!r} {kind!r}")
    env = {"VPN_SERVICE_PROVIDER": provider, "VPN_TYPE": kind}
    for field in fields + ["server_countries"]:
        value = request.get(field)
        if value in (None, ""):
            if field in VPN_OPTIONAL:
                continue
            raise ValueError(f"missing {field}")
        if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value):
            raise ValueError(f"{field} isn't text this can use")
        if field in ("private_key", "public_key", "preshared_key"):
            try:
                if len(base64.b64decode(value, validate=True)) != 32:
                    raise ValueError
            except (binascii.Error, ValueError):
                raise ValueError(f"{field} isn't a WireGuard key") from None
        elif field == "addresses":
            for part in value.split(","):
                ipaddress.ip_interface(part)
        elif field == "endpoint_ip":
            ipaddress.ip_address(value)
        elif field == "endpoint_port":
            if not value.isdigit() or not 0 < int(value) < 65536:
                raise ValueError("endpoint_port isn't a port")
        elif field == "server_countries":
            if not re.fullmatch(r"[A-Za-z][A-Za-z ,.'-]{0,199}", value):
                raise ValueError("server_countries isn't a list of countries")
        env[VPN_ENV[field]] = value
    return env


def with_service_env(compose: dict, image_prefix: str, env: dict[str, str], replacing: set[str]) -> dict:
    """The app's config with one service's settings in `replacing` swapped
    for `env`; every other setting of it, and every other service, kept."""
    moved = json.loads(json.dumps(compose))
    for service in (moved.get("services") or {}).values():
        if isinstance(service, dict) and str(service.get("image", "")).startswith(image_prefix):
            current = service.get("environment") or {}
            if isinstance(current, list):
                current = dict(str(item).split("=", 1) if "=" in str(item) else (str(item), "") for item in current)
            kept = {k: v for k, v in current.items() if k not in replacing}
            service["environment"] = {**kept, **env}
            return moved
    raise RuntimeError(f"this app has no {image_prefix.rstrip(':')} service")


def with_vpn(compose: dict, env: dict[str, str]) -> dict:
    """The app's config with gluetun's VPN settings replaced by `env`; every
    other gluetun setting kept."""
    try:
        return with_service_env(compose, GLUETUN_IMAGE, env, VPN_MANAGED)
    except RuntimeError:
        raise RuntimeError("this app has no VPN (gluetun) service") from None


def next_drive_name(pool_names, mnt: Path) -> str:
    """The lowest drive number free both as a pool and as a folder."""
    taken = {int(m.group(1)) for name in pool_names if (m := DRIVE_POOL.match(str(name)))}
    number = 1
    while number in taken or (mnt / f"drive{number}").exists():
        number += 1
    return f"drive{number}"


def with_drive(compose: dict, name: str, mnt: Path, prefix: str) -> dict:
    """The app's config with drive `name` mounted at /media/<name> in the
    backend, qBittorrent (both read-write) and Plex (read-only)."""
    moved = json.loads(json.dumps(compose))
    mount = f"{mnt / name}:/media/{name}"
    for service in (moved.get("services") or {}).values():
        if not isinstance(service, dict):
            continue
        image = str(service.get("image", ""))
        if our_image(image, prefix) == "obsidian-backend" or image.startswith(QBITTORRENT_IMAGE):
            wanted = mount
        elif image.startswith(PLEX_IMAGE):
            wanted = f"{mount}:ro"
        else:
            continue
        volumes = list(service.get("volumes") or [])
        if not any(str(v).startswith(f"{mnt / name}:") for v in volumes):
            volumes.append(wanted)
        service["volumes"] = volumes
    return moved


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
                 verify_seconds: int = VERIFY_SECONDS, sleep=time.sleep, log=None, mnt: Path = Path("/mnt")):
        self.mw = middleware
        self.mnt = mnt
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
            vpn_request = self.folder.read("vpn-request.json")
            if vpn_request is not None:
                self.folder.remove("vpn-request.json")  # claimed; it holds secrets, so gone either way
                self.apply_vpn(vpn_request)
            claim_request = self.folder.read("plex-claim-request.json")
            if claim_request is not None:
                self.folder.remove("plex-claim-request.json")
                self.apply_plex_claim(claim_request)
            drive_request = self.folder.read("drive-request.json")
            if drive_request is not None:
                self.folder.remove("drive-request.json")
                self.add_drive(drive_request)
            self.report_storage()
            self.report_network()
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

    def apply_vpn(self, request: dict) -> None:
        about = {
            "provider": request.get("provider") if request.get("provider") in VPN_PROVIDERS else None,
            "type": request.get("type") if request.get("type") in ("wireguard", "openvpn") else None,
            "server_countries": request.get("server_countries") if isinstance(request.get("server_countries"), str) else None,
        }

        def vpn_state(state: str, message: str) -> None:
            self.log(f"vpn {state}: {message}")
            self.folder.write("vpn-state.json", {"state": state, **about, "message": message, "at": now_iso()})

        try:
            env = vpn_env(request)
        except ValueError as exc:
            vpn_state("failed", f"Those VPN details couldn't be used ({exc}).")
            return
        vpn_state("applying", "Applying the VPN details.")
        try:
            self.mw.app_update(self.app, with_vpn(self.mw.app_config(self.app), env))
        except Exception as exc:  # noqa: BLE001
            vpn_state("failed", f"TrueNAS couldn't apply the VPN details: {_reason(exc)}")
            return
        vpn_state("applied", "Applied. The VPN connects within a minute.")

    # -- a box's drives --

    def report_storage(self) -> None:
        """What the app's Settings › Storage shows: the disks it could add,
        and how each of the box's drives is doing. Written every run."""
        try:
            unused = self.mw.unused_disks()
            pools = self.mw.pools()
        except Exception as exc:  # noqa: BLE001 — a report that can't be made says so
            self.folder.write("storage.json", {"at": now_iso(), "error": _reason(exc)})
            return
        report = {
            "at": now_iso(),
            "unused": [
                {
                    "name": d.get("name"), "serial": d.get("serial"), "model": d.get("model"),
                    "size": d.get("size"), "type": d.get("type"),
                    # Holds a pool from another system: adding it erases that.
                    "has_other_data": bool(d.get("exported_zpool")),
                }
                for d in unused
            ],
            "drives": [
                {
                    "id": p["name"], "status": p.get("status"), "healthy": bool(p.get("healthy")),
                    "size": p.get("size"), "allocated": p.get("allocated"), "free": p.get("free"),
                }
                for p in pools if DRIVE_POOL.match(str(p.get("name")))
            ],
        }
        self.folder.write("storage.json", report)

    def report_network(self) -> None:
        """The box's address on the home network, for the app to show
        beside obsidian.local: a phone that can't open .local names can
        use it. The box gets its address from the router, which can
        change it, so it's written every run."""
        try:
            summary, config = self.mw.network()
        except Exception as exc:  # noqa: BLE001
            self.folder.write("network.json", {"at": now_iso(), "error": _reason(exc)})
            return
        addresses = []
        for interface, ips in sorted((summary.get("ips") or {}).items()):
            if str(interface).startswith(VIRTUAL_INTERFACES):
                continue
            for ip in (ips or {}).get("IPV4") or []:
                address = str(ip).split("/")[0]
                if _IPV4.match(address) and not address.startswith(("127.", "169.254.")) and address not in addresses:
                    addresses.append(address)
        self.folder.write("network.json", {
            "at": now_iso(), "addresses": addresses, "hostname": config.get("hostname"),
            "mdns": bool((config.get("service_announcement") or {}).get("mdns")),
        })

    def add_drive(self, request: dict) -> None:
        def drive_state(state: str, message: str, drive: str | None = None) -> None:
            self.log(f"drive {state}: {message}")
            self.folder.write("drive-state.json", {"state": state, "drive": drive, "message": message, "at": now_iso()})

        disk, serial = request.get("disk"), request.get("serial")
        unused = {d.get("name"): d for d in self.mw.unused_disks()}
        found = unused.get(disk) if isinstance(disk, str) else None
        # Named and serial both, so a disk renamed by a restart (sdb is now
        # sdc) can't be erased in place of the one the person chose.
        if found is None or not serial or found.get("serial") != serial:
            drive_state("failed", "That drive isn't free to add any more. Check Settings › Storage and try again.")
            return
        if found.get("exported_zpool") and request.get("erase_other_data") is not True:
            drive_state("failed", "That drive holds data from another system, and erasing it wasn't confirmed.")
            return
        name = next_drive_name([p.get("name") for p in self.mw.pools()], self.mnt)
        label = f"Drive {name.removeprefix('drive')}"
        drive_state("adding", f"Setting up {label}.", name)
        try:
            self.mw.create_drive_pool(name, disk)
            top = self.mnt / name
            for folder in DRIVE_LAYOUT:
                (top / folder).mkdir(parents=True, exist_ok=True)
            self.mw.give_to_apps(str(top))
            # Last on the drive itself: the marker is what makes it count.
            marker = {"id": name, "label": label, "size_bytes": found.get("size"), "serial": serial, "added_at": now_iso()}
            (top / DRIVE_MARKER).write_text(json.dumps(marker, indent=2))
            self.mw.app_update(self.app, with_drive(self.mw.app_config(self.app), name, self.mnt, self.prefix))
        except Exception as exc:  # noqa: BLE001
            drive_state("failed", f"Setting up {label} didn't finish: {_reason(exc)}", name)
            return
        drive_state("added", f"{label} is ready.", name)

    def apply_plex_claim(self, request: dict) -> None:
        def claim_state(state: str, message: str) -> None:
            self.log(f"plex claim {state}: {message}")
            self.folder.write("plex-claim-state.json", {"state": state, "message": message, "at": now_iso()})

        token = request.get("claim_token")
        if not isinstance(token, str) or not _CLAIM_TOKEN.match(token):
            claim_state("failed", "That wasn't a Plex claim token.")
            return
        try:
            moved = with_service_env(self.mw.app_config(self.app), PLEX_IMAGE, {"PLEX_CLAIM": token}, {"PLEX_CLAIM"})
            self.mw.app_update(self.app, moved)
        except Exception as exc:  # noqa: BLE001
            claim_state("failed", f"TrueNAS couldn't restart Plex to claim it: {_reason(exc)}")
            return
        claim_state("applied", "Plex restarted to claim itself.")

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
