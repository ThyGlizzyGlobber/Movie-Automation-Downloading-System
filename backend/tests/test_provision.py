"""truenas/provision.py, the factory script: the qBittorrent settings each box
starts with, and filling in truenas/obsidian-box.yaml."""

import base64
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

TRUENAS = Path(__file__).resolve().parents[2] / "truenas"
spec = importlib.util.spec_from_file_location("provision", TRUENAS / "provision.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def test_the_password_hash_is_qbittorrent_s_own_format():
    salt = bytes(range(16))
    stored = provision.qbittorrent_password_hash("correct horse", salt)
    match = re.fullmatch(r"@ByteArray\(([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)\)", stored)
    assert match
    assert base64.b64decode(match[1]) == salt
    expected = hashlib.pbkdf2_hmac("sha512", b"correct horse", salt, 100_000)
    assert base64.b64decode(match[2]) == expected


def test_every_box_gets_its_own_salt():
    assert provision.qbittorrent_password_hash("same") != provision.qbittorrent_password_hash("same")


def test_qbittorrent_is_bound_to_the_tunnel():
    conf = provision.qbittorrent_conf("pw")
    assert "Session\\Interface=tun0" in conf and "Session\\InterfaceName=tun0" in conf
    assert "Session\\DefaultSavePath=/media" in conf  # each download names its drive under it
    assert "WebUI\\Username=admin" in conf
    assert '"pw"' not in conf and "=pw\n" not in conf  # only the hash is stored


def test_filling_the_box_leaves_nothing_unfilled():
    template = (TRUENAS / "obsidian-box.yaml").read_text()
    placeholders = sorted(set(re.findall(r"<([A-Z_]+)>", template)))
    filled = provision.fill(template, {name: f"value-{name}" for name in placeholders})
    assert "<" not in re.sub(r"#.*", "", filled)
    with pytest.raises(ValueError, match="unfilled: TZ"):
        provision.fill(template, {name: "x" for name in placeholders if name != "TZ"})


def test_qbittorrent_has_no_network_but_the_vpn_s():
    template = (TRUENAS / "obsidian-box.yaml").read_text()
    qbittorrent = template[template.index("  qbittorrent:"):template.index("  plex:")]
    assert 'network_mode: "service:gluetun"' in qbittorrent
    assert "ports:" not in qbittorrent


def test_a_setup_code_is_easy_to_read_off_a_card():
    codes = {provision.setup_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
        assert not set(code) & set("ILOU01")


# -- the whole run, against a fake TrueNAS --

GiB = 1024**3
TB = 1000**4


def disk(name, size=4 * TB, kind="HDD"):
    return {"name": name, "size": size, "type": kind, "serial": f"SN-{name}"}


APPS_SSD = disk("nvme1n1", 500 * 10**9, "SSD")


class FakeTrueNAS:
    def __init__(self, unused=(APPS_SSD,)):
        self.version, self.ram = "TrueNAS-25.10.7", 16 * GiB
        self.pools, self.apps, self.unused = {"boot-pool"}, [], list(unused)
        self.docker_pool = None
        self.datasets: set[str] = set()
        self.scripts: list[dict] = []
        self.compose = None
        self.calls: list[tuple] = []
        self.mnt = None

    def call(self, method, *params, job=False):
        self.calls.append((method, params))
        if method == "system.version":
            return self.version
        if method == "system.info":
            return {"physmem": self.ram}
        if method == "system.general.config":
            return {"timezone": "Australia/Sydney"}
        if method == "disk.get_unused":
            return self.unused
        if method == "pool.query":
            pools = [{"name": p} for p in sorted(self.pools)]
            return [p for p in pools if p["name"] == params[0][0][2]] if params else pools
        if method == "pool.create":
            name = params[0]["name"]
            self.pools.add(name)
            (self.mnt / name).mkdir(parents=True, exist_ok=True)  # TrueNAS mounts a new pool at /mnt/<name>
            used = set(params[0]["topology"]["data"][0]["disks"])
            self.unused = [d for d in self.unused if d["name"] not in used]
            return {}
        if method == "docker.config":
            return {"pool": self.docker_pool}
        if method == "docker.update":
            self.docker_pool = params[0]["pool"]
            return {}
        if method == "app.query":
            return [{"name": a} for a in self.apps if a == params[0][0][2]]
        if method == "app.create":
            self.apps.append(params[0]["app_name"])
            self.compose = {"services": {
                "backend": {"image": "ghcr.io/thyglizzyglobber/obsidian-backend:stable", "volumes": ["/mnt/apps/obsidian/data:/app/data"]},
                "qbittorrent": {"image": "lscr.io/linuxserver/qbittorrent:5.2.4_v2.0.15-ls479", "volumes": []},
                "plex": {"image": "plexinc/pms-docker:1.43.4.10903-e5521bd8c", "volumes": []},
            }}
            return {}
        if method == "app.config":
            return json.loads(json.dumps(self.compose))
        if method == "app.update":
            self.compose = params[1]["custom_compose_config"]
            return {}
        if method == "network.configuration.config":
            return {"hostname": "truenas", "service_announcement": {"netbios": False, "mdns": False, "wsd": True}}
        if method == "pool.dataset.query":
            return [{"id": params[0][0][2]}] if params[0][0][2] in self.datasets else []
        if method == "pool.dataset.create":
            self.datasets.add(params[0]["name"])
            (self.mnt / params[0]["name"]).mkdir(parents=True, exist_ok=True)
            return {}
        if method == "initshutdownscript.query":
            return [s for s in self.scripts if s["comment"] == params[0][0][2]]
        if method == "initshutdownscript.create":
            self.scripts.append({"id": len(self.scripts) + 1, **params[0]})
            return {}
        return {}

    def called(self, method):
        return [params for name, params in self.calls if name == method]


@pytest.fixture
def factory(tmp_path, monkeypatch):
    here = tmp_path / "here"
    here.mkdir()
    for name in ("obsidian-box.yaml", "obsidian-updater.py"):
        (here / name).write_bytes((TRUENAS / name).read_bytes())
    mnt, sysfs, dev = tmp_path / "mnt", tmp_path / "sys", tmp_path / "dev"
    mnt.mkdir()
    (sysfs / "module/zfs/parameters").mkdir(parents=True)
    dev.mkdir()
    monkeypatch.setattr(provision.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provision.os, "chown", lambda *a: None)
    installs = []
    monkeypatch.setattr(provision.subprocess, "run",
                        lambda cmd, **kw: installs.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(provision.Provisioner, "wait_for_site", lambda self, seconds=600: "1.1.0")
    nas = FakeTrueNAS()
    nas.mnt = mnt

    def make(**kwargs):
        return provision.Provisioner(nas, serial="OBS-0001", here=here, mnt=mnt, sysfs=sysfs, dev=dev,
                                     say=lambda _m: None, **kwargs)

    return nas, make, tmp_path, installs


def test_a_box_with_no_hard_drives_is_provisioned_end_to_end(factory):
    nas, make, tmp, installs = factory
    card = make().run()

    (apps,) = nas.called("pool.create")
    assert apps[0]["name"] == "apps" and apps[0]["topology"]["data"][0]["disks"] == ["nvme1n1"]
    assert nas.docker_pool == "apps"  # TrueNAS keeps its apps there
    names = [name for name, _ in nas.calls]
    assert names.index("docker.update") < names.index("app.create")

    (network,) = nas.called("network.configuration.update")
    assert network[0]["hostname"] == "obsidian"
    assert network[0]["service_announcement"] == {"netbios": False, "mdns": True, "wsd": True}
    (ui,) = nas.called("system.general.update")
    assert ui[0]["ui_port"] == 8080 and ui[0]["ui_httpsport"] == 8443

    (script,) = nas.scripts
    assert script["when"] == "POSTINIT" and script["command"].startswith(f"echo {8 * GiB} > ")
    assert (tmp / "sys/module/zfs/parameters/zfs_arc_max").read_text() == str(8 * GiB)

    assert {"apps/obsidian", "apps/obsidian/data", "apps/obsidian-system"} <= nas.datasets
    perms = {p[0]["path"]: p[0] for p in nas.called("filesystem.setperm")}
    assert perms[str(tmp / "mnt/apps/obsidian/qbittorrent")]["uid"] == 568
    assert perms[str(tmp / "mnt/apps/obsidian-system")]["mode"] == "700"
    conf = (tmp / "mnt/apps/obsidian/qbittorrent/qBittorrent/qBittorrent.conf").read_text()
    assert "Session\\Interface=tun0" in conf

    (create,) = nas.called("app.create")
    compose = create[0]["custom_compose_config_string"]
    assert "<" not in re.sub(r"#.*", "", compose)
    assert f'SETUP_CODE: "{card["setup_code"]}"' in compose
    assert 'MEDIA_DRIVES_ROOT: "/media"' in compose
    assert ":/media" not in re.sub(r"#.*", "", compose)  # no drive mounted until there's a drive
    assert 'TZ: "Australia/Sydney"' in compose

    assert nas.called("app.update") == []  # no drives to mount
    assert (tmp / "mnt/apps/obsidian-system/obsidian-updater.py").is_file()
    assert installs and installs[0][-3:] == ["install", "--app", "obsidian"]

    record = tmp / "mnt/apps/obsidian-system/provisioned.json"
    assert json.loads(record.read_text()) == card
    assert (record.stat().st_mode & 0o777) == 0o600
    assert card["storage"] == "No hard drives"
    assert card["card"] == f"card.html#OBS-0001/{card['setup_code']}"


def test_hard_drives_a_box_ships_with_are_added_as_drives_of_their_own(factory):
    nas, make, tmp, _ = factory
    nas.unused = [APPS_SSD, disk("sdb", 4 * TB)]
    card = make().run()

    created = [p[0] for p in nas.called("pool.create")]
    assert [(c["name"], c["topology"]["data"][0]) for c in created] == [
        ("apps", {"type": "STRIPE", "disks": ["nvme1n1"]}),
        ("drive1", {"type": "STRIPE", "disks": ["sdb"]}),
    ]
    top = tmp / "mnt/drive1"
    for folder in ("downloads/movies", "downloads/tv", "Movies", "TV Shows"):
        assert (top / folder).is_dir()
    marker = json.loads((top / ".obsidian-drive.json").read_text())
    assert (marker["id"], marker["label"], marker["size_bytes"]) == ("drive1", "Drive 1", 4 * TB)
    services = nas.compose["services"]
    assert f"{top}:/media/drive1" in services["backend"]["volumes"]
    assert f"{top}:/media/drive1" in services["qbittorrent"]["volumes"]
    assert f"{top}:/media/drive1:ro" in services["plex"]["volumes"]
    assert card["storage"] == "1 × 4.0 TB"


def test_plex_gets_the_gpu_when_the_box_has_one(factory):
    nas, make, tmp, _ = factory
    (tmp / "dev/dri").mkdir()
    make().run()
    compose = nas.called("app.create")[0][0]["custom_compose_config_string"]
    plex = compose[compose.index("  plex:"):]
    assert '    devices:\n      - "/dev/dri:/dev/dri"' in plex


def test_a_run_that_stopped_partway_can_run_again(factory):
    nas, make, _, _ = factory
    nas.pools.add("apps")
    nas.unused = []  # the SSD is the apps pool now
    nas.docker_pool = "apps"
    nas.datasets |= {"apps/obsidian"}
    nas.scripts.append({"id": 1, "comment": provision.ARC_COMMENT})
    make().run()
    assert nas.called("pool.create") == [] and nas.called("docker.update") == []
    assert "apps/obsidian" not in {p[0]["name"] for p in nas.called("pool.dataset.create")}
    assert nas.called("initshutdownscript.create") == []


@pytest.mark.parametrize("unused, says", [
    ([disk("sdb")], "no unused SSD for the apps"),
    ([APPS_SSD, disk("nvme2n1", 2 * TB, "SSD")], "more than one unused SSD"),
])
def test_without_one_ssd_for_the_apps_nothing_changes(factory, unused, says):
    nas, make, _, _ = factory
    nas.unused = unused
    with pytest.raises(SystemExit, match=says):
        make().run()
    assert not nas.called("pool.create") and not nas.called("network.configuration.update")


def test_the_apps_ssd_can_be_named_when_there_are_two(factory):
    nas, make, _, _ = factory
    nas.unused = [APPS_SSD, disk("nvme2n1", 2 * TB, "SSD")]
    make(apps_disk="nvme1n1").run()
    assert nas.called("pool.create")[0][0]["topology"]["data"][0]["disks"] == ["nvme1n1"]


@pytest.mark.parametrize("change, says", [
    ({"version": "TrueNAS-25.04.2"}, "TrueNAS 25.10"),
    ({"apps": ["obsidian"]}, "already installed"),
])
def test_a_box_that_isn_t_ready_is_refused_before_anything_changes(factory, change, says):
    nas, make, _, _ = factory
    for key, value in change.items():
        setattr(nas, key, value)
    with pytest.raises(SystemExit, match=says):
        make().run()
    assert not nas.called("network.configuration.update") and not nas.called("app.create")


def test_it_needs_root(factory, monkeypatch):
    _, make, _, _ = factory
    monkeypatch.setattr(provision.os, "geteuid", lambda: 501)
    with pytest.raises(SystemExit, match="root"):
        make().run()


def test_what_the_card_says_a_box_ships_with():
    assert provision.storage_summary([]) == "No hard drives"
    assert provision.storage_summary([4 * TB]) == "1 × 4.0 TB"
    assert provision.storage_summary([8 * TB, 8 * TB]) == "2 × 8.0 TB"
    assert provision.storage_summary([4 * TB, 8 * TB]) == "2 drives: 4.0 TB, 8.0 TB"


def test_the_memory_cap_is_half_the_ram_but_at_least_2_gib():
    assert provision.arc_cap_bytes(16 * GiB) == 8 * GiB
    assert provision.arc_cap_bytes(32 * GiB) == 16 * GiB
    assert provision.arc_cap_bytes(2 * GiB) == 2 * GiB
