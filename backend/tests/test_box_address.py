"""Where a box is on its home network (app/box/address.py), as the NAS
updater reports it, and the report itself (truenas/obsidian-updater.py)."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.box import address as box_address, drives
from app.core import config
from app.updater import channel

SCRIPT = Path(__file__).resolve().parents[2] / "truenas" / "obsidian-updater.py"
spec = importlib.util.spec_from_file_location("obsidian_updater_network", SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", str(tmp_path / "media"))


def report(age=0, **fields):
    at = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(timespec="seconds")
    channel.write_json(channel.updater_dir() / "network.json", {"at": at, **fields})


def test_a_box_is_its_name_and_the_address_the_router_gave_it(box):
    report(addresses=["192.168.1.40"], hostname="obsidian", mdns=True)
    assert box_address.addresses() == ["http://obsidian.local", "http://192.168.1.40"]


def test_an_old_address_isn_t_offered_the_box_may_have_moved(box):
    report(age=3600, addresses=["192.168.1.40"], hostname="obsidian", mdns=True)
    assert box_address.addresses() == ["http://obsidian.local"]


@pytest.mark.parametrize("fields", [
    {"addresses": ['192.168.1.40" onclick=x', "999.1.1.1", 7], "hostname": 'obsidian"><b>', "mdns": True},
    {"error": "middleware not running"},
])
def test_nothing_odd_in_the_report_is_passed_on(box, fields):
    report(**fields)
    assert box_address.addresses() == []


def test_an_install_that_isn_t_a_box_has_no_such_address(box, monkeypatch):
    report(addresses=["192.168.1.40"], hostname="truenas", mdns=True)
    monkeypatch.setattr(drives, "MEDIA_DRIVES_ROOT", None)
    assert box_address.addresses() == []


class Box:
    def __init__(self, summary=None, fail=False):
        self.summary, self.fail = summary, fail

    def network(self):
        if self.fail:
            raise RuntimeError("middleware not running")
        return self.summary, {"hostname": "obsidian", "service_announcement": {"mdns": True}}


class Folder:
    def __init__(self):
        self.written = {}

    def write(self, name, data):
        self.written[name] = data


def run_report(nas):
    updater = host.Updater.__new__(host.Updater)
    updater.mw, updater.folder = nas, Folder()
    updater.report_network()
    return updater.folder.written["network.json"]


def test_the_updater_reports_the_home_network_address_not_docker_s():
    written = run_report(Box({"ips": {
        "enp1s0": {"IPV4": ["192.168.1.40/24"], "IPV6": ["fe80::1/64"]},
        "docker0": {"IPV4": ["172.17.0.1/16"]},
        "br-4f2a": {"IPV4": ["172.16.0.1/24"]},
        "vethab12": {"IPV4": ["169.254.3.1/16"]},
    }, "default_routes": ["192.168.1.1"]}))
    assert (written["addresses"], written["hostname"], written["mdns"]) == (["192.168.1.40"], "obsidian", True)


def test_a_report_that_can_t_be_made_says_so():
    assert run_report(Box(fail=True))["error"] == "middleware not running"
