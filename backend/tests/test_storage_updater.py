"""truenas/obsidian-updater.py and a box's drives: the storage report it
writes every run, and adding a drive from a request — which erases a disk,
so it checks the disk is still the free one the person chose."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "truenas" / "obsidian-updater.py"
spec = importlib.util.spec_from_file_location("obsidian_updater_storage", SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

PREFIX = "ghcr.io/thyglizzyglobber/"
TB = 1000**4


def compose(data):
    return {"services": {
        "frontend": {"image": f"{PREFIX}obsidian-frontend:1.1.0", "ports": ["80:80"], "volumes": [f"{data}:/srv/backend-data:ro"]},
        "backend": {"image": f"{PREFIX}obsidian-backend:1.1.0", "volumes": [f"{data}:/app/data"]},
        "qbittorrent": {"image": "lscr.io/linuxserver/qbittorrent:5.2.4_v2.0.15-ls479", "network_mode": "service:gluetun",
                        "volumes": ["/mnt/apps/obsidian/qbittorrent:/config"]},
        "plex": {"image": "plexinc/pms-docker:1.43.4.10903-e5521bd8c", "network_mode": "host",
                 "volumes": ["/mnt/apps/obsidian/plex:/config"]},
        "gluetun": {"image": "qmcgaw/gluetun:v3.41.3"},
    }}


def disk(name, serial, size=8 * TB, **extra):
    return {"name": name, "serial": serial, "size": size, "type": "HDD", "model": "WDC WD80EFZZ", **extra}


class Box:
    def __init__(self, data, mnt):
        self.compose = compose(data)
        self.mnt = mnt
        self.unused = [disk("sdb", "WD-AAA")]
        self.pool_list = [{"name": "apps", "status": "ONLINE", "healthy": True}]
        self.created, self.owned, self.updates = [], [], []
        self.fail_create = False

    def app_config(self, app):
        return json.loads(json.dumps(self.compose))

    def app_update(self, app, moved):
        self.updates.append(moved)
        self.compose = moved

    def unused_disks(self):
        return list(self.unused)

    def pools(self):
        return list(self.pool_list)

    def create_drive_pool(self, name, disk_name):
        if self.fail_create:
            raise RuntimeError("disk busy")
        self.created.append((name, disk_name))
        (self.mnt / name).mkdir(parents=True)  # TrueNAS mounts a new pool at /mnt/<name>
        self.unused = [d for d in self.unused if d["name"] != disk_name]
        self.pool_list.append({"name": name, "status": "ONLINE", "healthy": True, "size": 8 * TB, "allocated": 0, "free": 8 * TB})

    def give_to_apps(self, path):
        self.owned.append(path)


@pytest.fixture
def box(tmp_path, monkeypatch):
    data, mnt = tmp_path / "data", tmp_path / "mnt"
    (data / "updater").mkdir(parents=True)
    mnt.mkdir()
    monkeypatch.setattr(host, "_get", lambda url, timeout=5: (200, b'{"version": "1.1.0"}'))
    the_box = Box(data, mnt)
    the_box.data = data
    return the_box


def run(box):
    host.Updater(box, "obsidian", verify_seconds=0, sleep=lambda _s: None, mnt=box.mnt).run()


def request(box, **body):
    (box.data / "updater" / "drive-request.json").write_text(json.dumps(body))


def updater_file(box, name):
    return json.loads((box.data / "updater" / name).read_text())


def test_every_run_reports_free_disks_and_the_box_s_drives(box):
    box.unused.append(disk("sdc", "WD-BBB", 4 * TB, exported_zpool="tank"))
    box.pool_list.append({"name": "drive1", "status": "DEGRADED", "healthy": False, "size": 4 * TB, "allocated": TB, "free": 3 * TB})
    run(box)
    report = updater_file(box, "storage.json")
    assert [(d["name"], d["serial"], d["has_other_data"]) for d in report["unused"]] == [("sdb", "WD-AAA", False), ("sdc", "WD-BBB", True)]
    assert report["drives"] == [{"id": "drive1", "status": "DEGRADED", "healthy": False, "size": 4 * TB, "allocated": TB, "free": 3 * TB}]
    # The apps pool is not one of the box's drives.


def test_adding_a_drive_sets_it_up_and_mounts_it_where_it_s_needed(box):
    request(box, disk="sdb", serial="WD-AAA")
    run(box)

    assert box.created == [("drive1", "sdb")]
    top = box.mnt / "drive1"
    for folder in ("downloads/movies", "downloads/tv", "Movies", "TV Shows"):
        assert (top / folder).is_dir()
    assert box.owned == [str(top)]
    marker = json.loads((top / ".obsidian-drive.json").read_text())
    assert (marker["id"], marker["label"], marker["size_bytes"], marker["serial"]) == ("drive1", "Drive 1", 8 * TB, "WD-AAA")

    services = box.compose["services"]
    assert f"{top}:/media/drive1" in services["backend"]["volumes"]
    assert f"{top}:/media/drive1" in services["qbittorrent"]["volumes"]
    assert f"{top}:/media/drive1:ro" in services["plex"]["volumes"]
    assert not any("drive1" in v for v in services["frontend"]["volumes"])
    assert updater_file(box, "drive-state.json")["state"] == "added"
    assert not (box.data / "updater" / "drive-request.json").exists()
    # The report afterwards shows it as a drive, not a free disk.
    report = updater_file(box, "storage.json")
    assert report["unused"] == [] and [d["id"] for d in report["drives"]] == ["drive1"]


def test_the_next_drive_takes_the_lowest_free_number(box):
    box.pool_list += [{"name": "drive1"}, {"name": "drive3"}]
    request(box, disk="sdb", serial="WD-AAA")
    run(box)
    assert box.created == [("drive2", "sdb")]
    assert updater_file(box, "drive-state.json")["message"] == "Drive 2 is ready."


@pytest.mark.parametrize("body", [
    {"disk": "sdb", "serial": "WD-OTHER"},  # renamed by a restart: sdb is now a different disk
    {"disk": "sdz", "serial": "WD-AAA"},  # not free
    {"disk": "sdb"},  # no serial to check
    {"disk": ["sdb"], "serial": "WD-AAA"},
])
def test_a_disk_that_isn_t_the_free_one_chosen_is_never_touched(box, body):
    request(box, **body)
    run(box)
    assert box.created == [] and box.updates == []
    assert updater_file(box, "drive-state.json")["state"] == "failed"


def test_a_disk_with_another_system_s_data_needs_that_confirmed(box):
    box.unused = [disk("sdb", "WD-AAA", exported_zpool="oldnas")]
    request(box, disk="sdb", serial="WD-AAA")
    run(box)
    assert box.created == []
    assert "data from another system" in updater_file(box, "drive-state.json")["message"]

    request(box, disk="sdb", serial="WD-AAA", erase_other_data=True)
    run(box)
    assert box.created == [("drive1", "sdb")]


def test_a_failure_partway_leaves_the_app_as_it_was_and_says_so(box):
    box.fail_create = True
    request(box, disk="sdb", serial="WD-AAA")
    run(box)
    assert box.updates == []
    state = updater_file(box, "drive-state.json")
    assert state["state"] == "failed" and "disk busy" in state["message"]


def test_mounting_a_drive_twice_doesn_t_add_it_twice(box):
    once = host.with_drive(box.compose, "drive1", box.mnt, PREFIX)
    twice = host.with_drive(once, "drive1", box.mnt, PREFIX)
    assert twice == once
