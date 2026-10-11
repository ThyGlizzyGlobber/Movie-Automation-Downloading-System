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


def test_qbittorrent_is_bound_to_the_tunnel_and_saves_into_the_shared_media():
    conf = provision.qbittorrent_conf("pw")
    assert "Session\\Interface=tun0" in conf and "Session\\InterfaceName=tun0" in conf
    assert "Session\\DefaultSavePath=/media/downloads" in conf
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


def hdd(name, size=8 * TB):
    return {"name": name, "size": size, "type": "HDD", "serial": f"SN-{name}"}


class FakeTrueNAS:
    def __init__(self, version="TrueNAS-25.10.7", ram=16 * GiB, pools=("tank",), apps=(), unused=()):
        self.version, self.ram, self.pools, self.apps = version, ram, set(pools), list(apps)
        self.unused = list(unused)
        self.datasets: set[str] = set()
        self.scripts: list[dict] = []
        self.calls: list[tuple] = []

    def call(self, method, *params, job=False):
        self.calls.append((method, params))
        if method == "system.version":
            return self.version
        if method == "system.info":
            return {"physmem": self.ram}
        if method == "system.general.config":
            return {"timezone": "Australia/Sydney"}
        if method == "pool.query":
            topology = {"data": [{"type": "RAIDZ1", "children": [{}, {}, {}]}]}
            return [{"name": p, "topology": topology} for p in self.pools if p == params[0][0][2]]
        if method == "disk.get_unused":
            return self.unused
        if method == "pool.create":
            self.pools.add(params[0]["name"])
            return {}
        if method == "app.query":
            return [{"name": a} for a in self.apps if a == params[0][0][2]]
        if method == "network.configuration.config":
            return {"hostname": "truenas", "service_announcement": {"netbios": False, "mdns": False, "wsd": True}}
        if method == "pool.dataset.query":
            return [{"id": params[0][0][2]}] if params[0][0][2] in self.datasets else []
        if method == "pool.dataset.create":
            self.datasets.add(params[0]["name"])
            return {}
        if method == "initshutdownscript.query":
            return [s for s in self.scripts if s["comment"] == params[0][0][2]]
        if method == "initshutdownscript.create":
            self.scripts.append({"id": len(self.scripts) + 1, **params[0]})
            return {}
        if method == "app.create":
            self.apps.append(params[0]["app_name"])
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
    for dataset in ("tank/obsidian/data", "tank/obsidian/qbittorrent", "tank/obsidian/plex",
                    "tank/obsidian/gluetun", "tank/media", "tank/obsidian-system"):
        (mnt / dataset).mkdir(parents=True)  # TrueNAS mounts a dataset when it's made
    (sysfs / "module/zfs/parameters").mkdir(parents=True)
    dev.mkdir()
    monkeypatch.setattr(provision.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provision.os, "chown", lambda *a: None)
    installs = []
    monkeypatch.setattr(provision.subprocess, "run",
                        lambda cmd, **kw: installs.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(provision.Provisioner, "wait_for_site", lambda self, seconds=600: "1.1.0")
    nas = FakeTrueNAS()

    def make(**kwargs):
        return provision.Provisioner(nas, "tank", serial="OBS-0001", here=here, mnt=mnt, sysfs=sysfs, dev=dev,
                                     say=lambda _m: None, **kwargs)

    return nas, make, tmp_path, installs


def test_a_fresh_box_is_provisioned_end_to_end(factory):
    nas, make, tmp, installs = factory
    card = make().run()

    (network,) = nas.called("network.configuration.update")
    assert network[0]["hostname"] == "obsidian"
    assert network[0]["service_announcement"] == {"netbios": False, "mdns": True, "wsd": True}
    (ui,) = nas.called("system.general.update")
    assert ui[0]["ui_port"] == 8080 and ui[0]["ui_httpsport"] == 8443

    (script,) = nas.scripts
    assert script["when"] == "POSTINIT" and script["command"].startswith(f"echo {8 * GiB} > ")
    assert (tmp / "sys/module/zfs/parameters/zfs_arc_max").read_text() == str(8 * GiB)

    assert {"tank/obsidian", "tank/media", "tank/obsidian-system", "tank/obsidian/data"} <= nas.datasets
    for folder in ("downloads", "Movies", "TV Shows"):
        assert (tmp / "mnt/tank/media" / folder).is_dir()
    perms = {p[0]["path"]: p[0] for p in nas.called("filesystem.setperm")}
    assert perms[str(tmp / "mnt/tank/media")]["uid"] == 568
    assert perms[str(tmp / "mnt/tank/obsidian-system")]["mode"] == "700"

    conf = (tmp / "mnt/tank/obsidian/qbittorrent/qBittorrent/qBittorrent.conf").read_text()
    assert "Session\\Interface=tun0" in conf

    (create,) = nas.called("app.create")
    compose = create[0]["custom_compose_config_string"]
    assert create[0]["app_name"] == "obsidian" and create[0]["custom_app"] is True
    assert "<" not in re.sub(r"#.*", "", compose)
    assert f'SETUP_CODE: "{card["setup_code"]}"' in compose
    assert "obsidian-backend:stable" in compose
    assert 'TZ: "Australia/Sydney"' in compose
    assert "/dev/dri" not in re.sub(r"#.*", "", compose)  # no GPU on this one

    assert (tmp / "mnt/tank/obsidian-system/obsidian-updater.py").is_file()
    assert installs and installs[0][-3:] == ["install", "--app", "obsidian"]

    record = tmp / "mnt/tank/obsidian-system/provisioned.json"
    assert json.loads(record.read_text()) == card
    assert (record.stat().st_mode & 0o777) == 0o600
    assert card["setup_link"] == f"http://obsidian.local/#/?code={card['setup_code']}"


def test_plex_gets_the_gpu_when_the_box_has_one(factory):
    nas, make, tmp, _ = factory
    (tmp / "dev/dri").mkdir()
    make().run()
    compose = nas.called("app.create")[0][0]["custom_compose_config_string"]
    plex = compose[compose.index("  plex:"):]
    assert '    devices:\n      - "/dev/dri:/dev/dri"' in plex


def test_a_run_that_stopped_partway_can_run_again(factory):
    nas, make, _, _ = factory
    nas.datasets |= {"tank/obsidian", "tank/media"}
    nas.scripts.append({"id": 1, "comment": provision.ARC_COMMENT})
    make().run()
    created = {p[0]["name"] for p in nas.called("pool.dataset.create")}
    assert "tank/media" not in created and "tank/obsidian/data" in created
    assert nas.called("initshutdownscript.create") == []
    assert len(nas.called("initshutdownscript.update")) == 1


@pytest.mark.parametrize("change, says", [
    ({"version": "TrueNAS-25.04.2"}, "TrueNAS 25.10"),
    ({"pools": ()}, "Found 0 unused hard drives"),
    ({"apps": ("obsidian",)}, "already installed"),
])
def test_a_box_that_isn_t_ready_is_refused_before_anything_changes(factory, change, says):
    nas, make, _, _ = factory
    for key, value in change.items():
        setattr(nas, key, value if key != "pools" else set(value))
    with pytest.raises(SystemExit, match=says):
        make().run()
    assert not nas.called("network.configuration.update") and not nas.called("app.create")


def test_it_needs_root(factory, monkeypatch):
    _, make, _, _ = factory
    monkeypatch.setattr(provision.os, "geteuid", lambda: 501)
    with pytest.raises(SystemExit, match="root"):
        make().run()


def test_a_box_s_pool_is_built_from_its_unused_hard_drives(factory):
    nas, make, _, _ = factory
    nas.pools = set()
    nas.unused = [hdd("sdc"), hdd("sda"), hdd("sdb", 8 * TB - 3_000_000), {"name": "nvme0n1", "size": 256 * 10**9, "type": "SSD"}]
    card = make().run()
    (create,) = nas.called("pool.create")
    assert create[0]["name"] == "tank"
    assert create[0]["topology"] == {"data": [{"type": "RAIDZ1", "disks": ["sda", "sdb", "sdc"]}]}  # the SSD left out
    assert card["storage"] == "3 × 8.0 TB, RAIDZ1"
    # Built before anything goes on it.
    names = [name for name, _ in nas.calls]
    assert names.index("pool.create") < names.index("pool.dataset.create") < names.index("app.create")


def test_four_drives_make_one_four_drive_pool(factory):
    nas, make, _, _ = factory
    nas.pools = set()
    nas.unused = [hdd(n) for n in ("sda", "sdb", "sdc", "sdd")]
    card = make().run()
    assert len(nas.called("pool.create")[0][0]["topology"]["data"][0]["disks"]) == 4
    assert card["storage"] == "4 × 8.0 TB, RAIDZ1"


@pytest.mark.parametrize("unused, says", [
    ([hdd("sda"), hdd("sdb")], "Found 2 unused hard drives; a box needs at least 3"),
    ([hdd("sda"), hdd("sdb"), hdd("sdc", 4 * TB)], "have to be the same size; these aren't: sda 8.0 TB, sdb 8.0 TB, sdc 4.0 TB"),
    ([{"name": n, "size": 8 * TB, "type": "SSD"} for n in ("a", "b", "c")], "Found 0 unused hard drives"),
])
def test_drives_that_won_t_make_a_box_stop_it_before_anything_changes(factory, unused, says):
    nas, make, _, _ = factory
    nas.pools = set()
    nas.unused = unused
    with pytest.raises(SystemExit, match=re.escape(says)):
        make().run()
    assert not nas.called("pool.create") and not nas.called("network.configuration.update")


def test_an_existing_pool_is_used_as_it_is(factory):
    nas, make, _, _ = factory
    card = make().run()
    assert nas.called("pool.create") == [] and nas.called("disk.get_unused") == []
    assert card["storage"] == "3 drives, RAIDZ1"


def test_the_memory_cap_is_half_the_ram_but_at_least_2_gib():
    assert provision.arc_cap_bytes(16 * GiB) == 8 * GiB
    assert provision.arc_cap_bytes(32 * GiB) == 16 * GiB
    assert provision.arc_cap_bytes(2 * GiB) == 2 * GiB
