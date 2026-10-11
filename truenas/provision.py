#!/usr/bin/env python3
"""Turns a fresh TrueNAS 25.10 install into an Obsidian box. Run once per box
at the factory, as root in the TrueNAS shell, before it ships:

    python3 provision.py --serial OBS-0001

with obsidian-box.yaml and obsidian-updater.py beside it (fetch all three
from the repo's truenas/ folder).

A box boots TrueNAS from one small SSD, keeps its apps on a second internal
SSD, and has four 3.5" bays for hard drives. A box may ship with no hard
drives, or with some; each is its own storage (see backend/app/drives.py),
and the buyer adds more from Settings › Storage. In order, it:

  1. checks it's on TrueNAS 25.10, as root, with no Obsidian app yet, and
     an SSD for the apps (unused, or already the "apps" pool)
  2. builds that SSD into the "apps" pool and has TrueNAS keep its apps
     there — TrueNAS can't run apps from its own boot drive
  3. names the box `obsidian` and announces it on the network, so
     http://obsidian.local reaches it, and moves TrueNAS's own web page to
     port 8080 so Obsidian can have the standard one
  4. caps ZFS's memory cache at half the RAM, now and after every reboot —
     left alone it takes nearly all of it and the box falls over
  5. creates the app's datasets on the apps pool
  6. makes the box's secrets — its setup code, qBittorrent's password,
     the VPN status key — and seeds qBittorrent's settings
  7. installs the app (truenas/obsidian-box.yaml), with the GPU passed to
     Plex when there is one, and waits for the site to answer
  8. adds each hard drive the box ships with as a drive of its own, exactly
     as the updater adds one later
  9. installs the updater, in a root-only dataset the app never mounts
 10. prints what goes on the box's card, and keeps a root-only record

Each step checks what's already done, so a run that stopped partway can be
run again. Standard library only, so it runs on TrueNAS's own Python.
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "obsidian"
APPS_POOL = "apps"
HOSTNAME = "obsidian"
TRUENAS_UI_PORT = 8080
TRUENAS_UI_HTTPS_PORT = 8443
APPS_UID = 568  # TrueNAS's own "apps" user, which qBittorrent and Plex run as
ARC_COMMENT = "Obsidian: cap the ZFS memory cache"
TRUENAS_SERIES = "25.10"

# The setup code on a box's card: typed by a person, so no letters that
# pass for others (no I, L, O, U; no 0 or 1). Twelve of these is about 60
# bits, and setup only answers on the home network, rate limited.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def tb(size: int) -> str:
    return f"{size / 1000**4:.1f} TB"


def storage_summary(sizes: list[int]) -> str:
    """What the card says the box ships with."""
    if not sizes:
        return "No hard drives"
    if len(set(sizes)) == 1:
        return f"{len(sizes)} × {tb(sizes[0])}"
    return f"{len(sizes)} drives: {', '.join(tb(s) for s in sizes)}"


def load_updater(here: Path):
    """obsidian-updater.py, beside this script: the one place drives are
    set up, so a drive added at the factory is the same as one added later."""
    spec = importlib.util.spec_from_file_location("obsidian_updater", here / "obsidian-updater.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_code() -> str:
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return "-".join(code[i:i + 4] for i in range(0, 12, 4))


# qBittorrent's own password format: PBKDF2-HMAC-SHA512, 100,000 rounds,
# a 16-byte salt, stored as "@ByteArray(salt:key)" in base64.
_PBKDF2_ROUNDS = 100_000


def qbittorrent_password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()})"


def qbittorrent_conf(password: str) -> str:
    """The qBittorrent.conf a box starts with: its WebUI password (made per
    box), saving under /media (each download names its drive), and bound to the VPN tunnel's
    interface — a second lock behind gluetun's firewall, so qBittorrent
    won't use any other interface even if one appeared."""
    return f"""[LegalNotice]
Accepted=true

[BitTorrent]
Session\\DefaultSavePath=/media
Session\\TempPathEnabled=false
Session\\Interface=tun0
Session\\InterfaceName=tun0
Session\\AnonymousModeEnabled=true

[Preferences]
General\\Locale=en
WebUI\\Port=8080
WebUI\\Username=admin
WebUI\\Password_PBKDF2="{qbittorrent_password_hash(password)}"
WebUI\\LocalHostAuth=true
WebUI\\HostHeaderValidation=false
WebUI\\CSRFProtection=true
"""


def fill(template: str, values: dict[str, str]) -> str:
    """truenas/obsidian-box.yaml with its placeholders filled in. Refuses
    to leave any unfilled."""
    for key, value in values.items():
        template = template.replace(f"<{key}>", value)
    left = sorted({part.split(">", 1)[0] for part in template.split("<")[1:] if ">" in part and part.split(">", 1)[0].isupper()})
    if left:
        raise ValueError(f"unfilled: {', '.join(left)}")
    return template


def with_gpu(compose_yaml: str) -> str:
    """Plex given the box's GPU, for hardware transcoding."""
    marker = "    # provision.py adds /dev/dri here"
    if marker not in compose_yaml:
        raise ValueError("obsidian-box.yaml has no place marked for Plex's GPU")
    return compose_yaml.replace(marker, '    devices:\n      - "/dev/dri:/dev/dri"\n' + marker, 1)


def arc_cap_bytes(ram_bytes: int) -> int:
    """Half the RAM, at least 2 GiB. On the development NAS (16 GB) that's
    the 8 GB cap that ended its crashes."""
    return max(2 * 1024**3, ram_bytes // 2)


class Middleware:
    """TrueNAS's API over its local socket, as root."""

    def __init__(self):
        try:
            from truenas_api_client import Client
        except ImportError:
            from middlewared.client import Client
        self._client = Client

    def call(self, method: str, *params, job: bool = False):
        with self._client() as c:
            return c.call(method, *params, job=job)


class Provisioner:
    def __init__(self, mw, *, serial: str, channel: str = "stable", tz: str | None = None,
                 apps_disk: str | None = None, here: Path | None = None, mnt: Path = Path("/mnt"),
                 sysfs: Path = Path("/sys"), dev: Path = Path("/dev"), site: str = "http://127.0.0.1",
                 say=print, sleep=time.sleep):
        self.mw = mw
        self.serial = serial
        self.channel = channel
        self.tz = tz
        self.apps_disk = apps_disk
        self.here = here or Path(__file__).resolve().parent
        self.mnt = mnt
        self.sysfs = sysfs
        self.dev = dev
        self.site = site
        self.say = say
        self.sleep = sleep

    def path(self, dataset: str) -> Path:
        return self.mnt / dataset

    # -- 1 --

    def preflight(self) -> None:
        if os.geteuid() != 0:
            raise SystemExit("Run this as root, in the TrueNAS shell.")
        version = str(self.mw.call("system.version"))
        if f"-{TRUENAS_SERIES}" not in version:
            raise SystemExit(f"This is {version}; a box is built on TrueNAS {TRUENAS_SERIES}.")
        if self.mw.call("app.query", [["name", "=", APP_NAME]]):
            raise SystemExit(f"An app called {APP_NAME!r} is already installed; this box looks provisioned.")
        for needed in ("obsidian-box.yaml", "obsidian-updater.py"):
            if not (self.here / needed).is_file():
                raise SystemExit(f"{needed} has to be beside this script.")
        self.automatic_addressing()
        unused = self.mw.call("disk.get_unused")
        self.ssd = None
        if not self.mw.call("pool.query", [["name", "=", APPS_POOL]]):
            ssds = [d for d in unused if d.get("type") == "SSD" and (self.apps_disk in (None, d.get("name")))]
            if not ssds:
                raise SystemExit("There's no unused SSD for the apps. A box needs a second internal SSD beside the boot drive.")
            if len(ssds) > 1:
                names = ", ".join(f"{d['name']} ({tb(d['size'])})" for d in ssds)
                raise SystemExit(f"There's more than one unused SSD ({names}). Name the one for the apps with --apps-disk.")
            self.ssd = ssds[0]
        # Every unused hard drive is one the box ships with.
        self.hard_drives = sorted((d for d in unused if d.get("type") == "HDD"), key=lambda d: d["name"])
        self.say(f"TrueNAS {version}: ready. Ships with: {storage_summary([d['size'] for d in self.hard_drives])}.")

    def automatic_addressing(self) -> None:
        """A box gets its address from the buyer's router, never one fixed
        here: a fixed address is one on this network, and on the buyer's
        (often another range, or a new router a year later) the box would
        be unreachable with no screen to fix it from. The router hands the
        same box the same address back anyway, and obsidian.local finds it
        whatever it is. So a box set up by hand with a fixed address, or a
        fixed gateway or DNS server, is stopped here to be put back."""
        fixed = [i.get("name") for i in self.mw.call("interface.query")
                 if not i.get("ipv4_dhcp") and i.get("aliases")]
        if fixed:
            raise SystemExit(
                f"{', '.join(map(str, fixed))} has a fixed address. A box ships getting its address from the "
                "router: set it back to DHCP under Network › Interfaces, then run this again.")
        config = self.mw.call("network.configuration.config")
        set_by_hand = [key for key in ("ipv4gateway", "nameserver1", "nameserver2", "nameserver3") if config.get(key)]
        if set_by_hand:
            raise SystemExit(
                f"The network has {', '.join(set_by_hand)} set by hand. A box takes them from the router: clear them "
                "under Network › Global configuration, then run this again.")

    # -- 2 --

    def apps_pool(self) -> None:
        if self.ssd is not None:
            self.say(f"Building the apps pool on {self.ssd['name']} ({tb(self.ssd['size'])})...")
            self.mw.call("pool.create", {
                "name": APPS_POOL, "encryption": False,
                "topology": {"data": [{"type": "STRIPE", "disks": [self.ssd["name"]]}]},
            }, job=True)
        if (self.mw.call("docker.config") or {}).get("pool") != APPS_POOL:
            self.mw.call("docker.update", {"pool": APPS_POOL}, job=True)
        self.say("Apps live on the apps pool.")

    # -- 3 --

    def network(self) -> None:
        current = self.mw.call("network.configuration.config")
        announce = {**(current.get("service_announcement") or {}), "mdns": True}
        self.mw.call("network.configuration.update", {"hostname": HOSTNAME, "service_announcement": announce})
        self.mw.call("system.general.update", {
            "ui_port": TRUENAS_UI_PORT, "ui_httpsport": TRUENAS_UI_HTTPS_PORT, "ui_httpsredirect": False,
        })
        self.say(f"Named {HOSTNAME}.local; TrueNAS's own page moved to port {TRUENAS_UI_PORT}.")

    # -- 4 --

    def memory(self) -> None:
        cap = arc_cap_bytes(int(self.mw.call("system.info")["physmem"]))
        command = f"echo {cap} > /sys/module/zfs/parameters/zfs_arc_max"
        existing = self.mw.call("initshutdownscript.query", [["comment", "=", ARC_COMMENT]])
        data = {"type": "COMMAND", "command": command, "when": "POSTINIT", "enabled": True,
                "timeout": 10, "comment": ARC_COMMENT}
        if existing:
            self.mw.call("initshutdownscript.update", existing[0]["id"], data)
        else:
            self.mw.call("initshutdownscript.create", data)
        (self.sysfs / "module/zfs/parameters/zfs_arc_max").write_text(str(cap))
        self.say(f"ZFS memory cache capped at {cap / 1024**3:.1f} GiB.")

    # -- 5 --

    def datasets(self) -> dict[str, Path]:
        names = {
            "data": f"{APPS_POOL}/obsidian/data",
            "qbittorrent": f"{APPS_POOL}/obsidian/qbittorrent",
            "plex": f"{APPS_POOL}/obsidian/plex",
            "gluetun": f"{APPS_POOL}/obsidian/gluetun",
            # The updater lives here: root only, and never mounted by the app.
            "system": f"{APPS_POOL}/obsidian-system",
        }
        for dataset in [f"{APPS_POOL}/obsidian", *names.values()]:
            if not self.mw.call("pool.dataset.query", [["id", "=", dataset]]):
                self.mw.call("pool.dataset.create", {"name": dataset, "type": "FILESYSTEM"})
        paths = {key: self.path(name) for key, name in names.items()}
        # qBittorrent and Plex run as TrueNAS's apps user; the backend and
        # gluetun as root. The updater's home is root's alone.
        for key in ("qbittorrent", "plex"):
            self.mw.call("filesystem.setperm", {
                "path": str(paths[key]), "uid": APPS_UID, "gid": APPS_UID, "mode": "775",
                "options": {"recursive": True, "stripacl": True},
            }, job=True)
        self.mw.call("filesystem.setperm", {
            "path": str(paths["system"]), "uid": 0, "gid": 0, "mode": "700", "options": {"stripacl": True},
        }, job=True)
        self.say("The app's datasets are ready on the apps pool.")
        return paths

    # -- 6 --

    def secrets(self, paths: dict[str, Path]) -> dict[str, str]:
        made = {
            "setup_code": setup_code(),
            "qbit_password": secrets.token_urlsafe(24),
            "gluetun_api_key": secrets.token_urlsafe(24),
        }
        conf = paths["qbittorrent"] / "qBittorrent" / "qBittorrent.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(qbittorrent_conf(made["qbit_password"]))
        for path in (conf.parent, conf):
            os.chown(path, APPS_UID, APPS_UID)
        self.say("Secrets made; qBittorrent's settings seeded.")
        return made

    # -- 7 --

    def app(self, paths: dict[str, Path], made: dict[str, str]) -> str:
        compose = fill((self.here / "obsidian-box.yaml").read_text(), {
            "CHANNEL": self.channel,
            "DATA_PATH": str(paths["data"]),
            "QBIT_CONFIG_PATH": str(paths["qbittorrent"]),
            "PLEX_CONFIG_PATH": str(paths["plex"]),
            "GLUETUN_PATH": str(paths["gluetun"]),
            "QBIT_PASSWORD": made["qbit_password"],
            "GLUETUN_API_KEY": made["gluetun_api_key"],
            "SETUP_CODE": made["setup_code"],
            "TZ": self.tz or self.mw.call("system.general.config").get("timezone") or "UTC",
        })
        gpu = (self.dev / "dri").exists()
        if gpu:
            compose = with_gpu(compose)
        self.say("Installing the app; TrueNAS downloads the images first, which takes a few minutes...")
        self.mw.call("app.create", {"app_name": APP_NAME, "custom_app": True, "custom_compose_config_string": compose}, job=True)
        version = self.wait_for_site()
        self.say(f"Obsidian {version} is up{' (Plex has the GPU)' if gpu else ''}.")
        return version

    def wait_for_site(self, seconds: int = 600) -> str:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.site}/api/health", timeout=5) as response:
                    return json.loads(response.read()).get("version") or "unknown"
            except (OSError, ValueError, urllib.error.URLError):
                self.sleep(5)
        raise SystemExit("The app was installed but the site never answered. Check Apps › obsidian in TrueNAS.")

    # -- 8 --

    def drives(self) -> list[int]:
        """Each hard drive the box ships with, as a drive of its own: the
        updater's own steps (pool, layout, owner, marker), then one app
        update mounting them all. Sizes, for the card."""
        if not self.hard_drives:
            self.say("No hard drives to add; the buyer adds them from Settings › Storage.")
            return []
        host = load_updater(self.here)
        compose = self.mw.call("app.config", APP_NAME)
        pools = [p.get("name") for p in self.mw.call("pool.query")]
        for disk in self.hard_drives:
            name = host.next_drive_name(pools, self.mnt)
            pools.append(name)
            label = f"Drive {name.removeprefix('drive')}"
            self.mw.call("pool.create", {
                "name": name, "encryption": False, "topology": {"data": [{"type": "STRIPE", "disks": [disk["name"]]}]},
            }, job=True)
            top = self.mnt / name
            for folder in host.DRIVE_LAYOUT:
                (top / folder).mkdir(parents=True, exist_ok=True)
            self.mw.call("filesystem.setperm", {
                "path": str(top), "uid": APPS_UID, "gid": APPS_UID, "mode": "775",
                "options": {"recursive": True, "stripacl": True},
            }, job=True)
            (top / host.DRIVE_MARKER).write_text(json.dumps({
                "id": name, "label": label, "size_bytes": disk["size"], "serial": disk.get("serial"),
                "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, indent=2))
            compose = host.with_drive(compose, name, self.mnt, host.IMAGE_PREFIX)
            self.say(f"{label}: {disk['name']}, {tb(disk['size'])}.")
        self.mw.call("app.update", APP_NAME, {"custom_compose_config": compose}, job=True)
        return [d["size"] for d in self.hard_drives]

    # -- 9 --

    def updater(self, paths: dict[str, Path]) -> None:
        script = paths["system"] / "obsidian-updater.py"
        script.write_bytes((self.here / "obsidian-updater.py").read_bytes())
        result = subprocess.run([sys.executable, str(script), "install", "--app", APP_NAME], capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(f"The updater wouldn't install:\n{result.stdout}{result.stderr}")
        self.say("Updater installed: updates, VPN changes and new drives from the app are applied every minute.")

    # -- 10 --

    def record(self, paths: dict[str, Path], made: dict[str, str], version: str, storage: str) -> dict:
        card = {
            "serial": self.serial,
            "address": f"http://{HOSTNAME}.local",
            "setup_code": made["setup_code"],
            "setup_link": f"http://{HOSTNAME}.local/#/?code={made['setup_code']}",
            "truenas_admin": f"http://{HOSTNAME}.local:{TRUENAS_UI_PORT}",
            # Opens truenas/card.html with this box's serial and code filled in.
            "card": f"card.html#{self.serial}/{made['setup_code']}",
            "storage": storage,
            "version": version,
            "channel": self.channel,
            "provisioned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        path = paths["system"] / "provisioned.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(card, handle, indent=2)
        return card

    def run(self) -> dict:
        self.preflight()
        self.apps_pool()
        self.network()
        self.memory()
        paths = self.datasets()
        made = self.secrets(paths)
        version = self.app(paths, made)
        storage = storage_summary(self.drives())
        self.updater(paths)
        card = self.record(paths, made, version, storage)
        self.say("")
        self.say(f"Box {card['serial']} is ready. Its card: open {card['card']} (truenas/card.html) and print it.")
        self.say("On the card:")
        self.say(f"  Address:     {card['address']}")
        self.say(f"  Setup code:  {card['setup_code']}")
        self.say(f"  QR code to:  {card['setup_link']}")
        self.say(f"  Ships with:  {card['storage']}")
        self.say(f"  (TrueNAS's own page, for support: {card['truenas_admin']})")
        self.say(f"Kept in {paths['system'] / 'provisioned.json'} (root only).")
        return card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turns a fresh TrueNAS 25.10 install into an Obsidian box.")
    parser.add_argument("--serial", required=True, help="this box's serial, for its card and your records")
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--tz", help="time zone, like Australia/Sydney (default: TrueNAS's own)")
    parser.add_argument("--apps-disk", help="the SSD for the apps, when there's more than one unused (like nvme1n1)")
    args = parser.parse_args(argv)
    Provisioner(Middleware(), serial=args.serial, channel=args.channel, tz=args.tz, apps_disk=args.apps_disk).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
