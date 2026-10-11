"""truenas/obsidian-updater.py, the script that runs as root on the NAS and
installs the updates the app asks for. TrueNAS and the site are faked; the
data folder is real, including the hostile cases a root script reading a
folder the app can write has to survive."""

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "truenas" / "obsidian-updater.py"
spec = importlib.util.spec_from_file_location("obsidian_updater", SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

OLD, NEW = "1.0.46", "1.0.47"
PREFIX = "ghcr.io/thyglizzyglobber/"


def compose(data, tag="stable"):
    return {
        "services": {
            "frontend": {"image": f"{PREFIX}obsidian-frontend:{tag}", "ports": ["8095:80"],
                         "volumes": [f"{data}:/srv/backend-data:ro"]},
            "backend": {"image": f"{PREFIX}obsidian-backend:{tag}", "volumes": [f"{data}:/app/data", "/mnt/tv:/tv-library"]},
            "cloudflared": {"image": "cloudflare/cloudflared:latest"},
        },
        "volumes": {"tmdb-cache": {}},
    }


class Box:
    """TrueNAS plus the running app: an update moves the site to the version
    the backend's image names, unless that version is broken."""

    def __init__(self, data):
        self.compose = compose(data)
        self.running = OLD
        self.broken: set[str] = set()
        self.refuse: str | None = None
        self.updates: list[dict] = []
        self.cron: list[dict] = []

    def app_config(self, app):
        assert app == "obsidian"
        return json.loads(json.dumps(self.compose))

    def app_update(self, app, moved):
        self.updates.append(moved)
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.compose = moved
        tag = moved["services"]["backend"]["image"].rsplit(":", 1)[1]
        self.running = OLD if tag == "stable" else tag

    def get(self, url, timeout=5):
        if self.running in self.broken:
            return 0, b""
        if url.endswith("/api/health"):
            return 200, json.dumps({"status": "ok", "version": self.running}).encode()
        return 200, b"<html>"

    def cron_jobs(self, description):
        return [job for job in self.cron if job["description"] == description]

    def cron_save(self, job_id, data):
        if job_id is None:
            self.cron.append({"id": len(self.cron) + 1, **data})
        else:
            self.cron[job_id - 1] = {"id": job_id, **data}


@pytest.fixture
def box(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    the_box = Box(data)
    monkeypatch.setattr(host, "_get", the_box.get)
    the_box.data = data
    return the_box


def updater(box):
    return host.Updater(box, "obsidian", verify_seconds=0, sleep=lambda _s: None)


def request(box, version=NEW, backup="obsidian-20261011T000000Z-1.0.46-to-1.0.47.db"):
    folder = box.data / "updater"
    folder.mkdir(exist_ok=True)
    (folder / "request.json").write_text(json.dumps({"version": version, "backup": backup}))


def state(box):
    return json.loads((box.data / "updater" / "state.json").read_text())


# -- reading the app --


def test_the_data_folder_and_site_come_from_the_app_itself(box):
    config = box.compose
    assert host.data_dir(config, PREFIX) == str(box.data)
    assert host.site_url(config, PREFIX) == "http://127.0.0.1:8095"


def test_long_form_mounts_and_ports_are_read_too():
    config = {"services": {
        "backend": {"image": f"{PREFIX}obsidian-backend:1.0.0",
                    "volumes": [{"type": "bind", "source": "/mnt/apps/obsidian", "target": "/app/data"}]},
        "frontend": {"image": f"{PREFIX}obsidian-frontend:1.0.0", "ports": [{"target": 80, "published": 9000}]},
    }}
    assert host.data_dir(config, PREFIX) == "/mnt/apps/obsidian"
    assert host.site_url(config, PREFIX) == "http://127.0.0.1:9000"


def test_a_git_pull_install_is_read_by_service_name():
    config = {"services": {
        "backend": {"build": {"context": "/repo/backend"}, "volumes": ["/mnt/SSD/AppData/data:/app/data"]},
        "frontend": {"build": {"context": "/repo/frontend"}, "ports": ["8095:80"]},
    }}
    assert host.data_dir(config, PREFIX) == "/mnt/SSD/AppData/data"
    assert host.site_url(config, PREFIX) == "http://127.0.0.1:8095"


def test_a_named_volume_is_not_a_folder_it_can_use():
    config = {"services": {"backend": {"image": f"{PREFIX}obsidian-backend:1", "volumes": ["data:/app/data"]}}}
    with pytest.raises(RuntimeError, match="named volume"):
        host.data_dir(config, PREFIX)


def test_retagging_moves_only_our_images(box):
    moved, before = host.retag(box.compose, NEW, PREFIX)
    assert moved["services"]["backend"]["image"] == f"{PREFIX}obsidian-backend:{NEW}"
    assert moved["services"]["frontend"]["image"] == f"{PREFIX}obsidian-frontend:{NEW}"
    assert moved["services"]["cloudflared"]["image"] == "cloudflare/cloudflared:latest"
    assert set(before) == {"backend", "frontend"}
    assert box.compose["services"]["backend"]["image"].endswith(":stable")  # untouched
    assert host.our_image("ghcr.io/someone-else/obsidian-backend:1", PREFIX) is None


# -- a run --


def test_every_run_leaves_a_heartbeat_and_does_nothing_else(box):
    updater(box).run()
    beat = json.loads((box.data / "updater" / "heartbeat.json").read_text())
    assert beat["script"] == host.SCRIPT_VERSION
    assert box.updates == []
    assert not (box.data / "updater" / "state.json").exists()


def test_a_requested_update_that_comes_up_is_done(box):
    request(box)
    updater(box).run()
    assert state(box)["state"] == "done"
    assert state(box)["message"] == f"Updated to {NEW}."
    assert box.running == NEW
    assert not (box.data / "updater" / "request.json").exists()  # claimed
    assert not (box.data / "updater" / "restore.json").exists()


def test_one_that_never_comes_up_is_rolled_back_with_its_data(box):
    box.broken.add(NEW)
    request(box)
    updater(box).run()
    final = state(box)
    assert final["state"] == "rolled_back"
    assert box.running == OLD
    assert [u["services"]["backend"]["image"].rsplit(":", 1)[1] for u in box.updates] == [NEW, "stable"]
    assert final["message"].endswith(f"so Obsidian went back to {OLD}.")
    restore = json.loads((box.data / "updater" / "restore.json").read_text())
    assert restore == {"backup": "obsidian-20261011T000000Z-1.0.46-to-1.0.47.db", "version": OLD}


def test_truenas_refusing_with_the_old_version_still_up_changes_nothing(box):
    box.refuse = "image pull failed"
    request(box)
    updater(box).run()
    final = state(box)
    assert final["state"] == "failed"
    assert "nothing changed" in final["message"] and "image pull failed" in final["message"]
    assert len(box.updates) == 1


def test_an_app_without_our_images_is_left_alone(box):
    box.compose["services"]["backend"]["image"] = "nginx:alpine"
    box.compose["services"]["frontend"]["image"] = "nginx:alpine"
    request(box)
    updater(box).run()
    assert state(box)["state"] == "failed"
    assert box.updates == []


@pytest.mark.parametrize("version", ["latest", "1.0.47; rm -rf /", "../1.0.47", 147, None])
def test_a_request_that_isnt_a_version_is_refused(box, version):
    request(box, version=version)
    updater(box).run()
    assert state(box)["state"] == "failed"
    assert box.updates == []


def test_a_backup_name_that_isnt_one_is_never_passed_on(box):
    box.broken.add(NEW)
    request(box, backup="../../etc/passwd")
    updater(box).run()
    assert state(box)["state"] == "rolled_back"
    assert not (box.data / "updater" / "restore.json").exists()


# -- after an interruption (the NAS restarted mid-update) --


def test_an_update_left_verifying_is_verified(box):
    box.running = NEW
    (box.data / "updater").mkdir()
    (box.data / "updater" / "state.json").write_text(json.dumps(
        {"state": "verifying", "from_version": OLD, "to_version": NEW}))
    updater(box).run()
    assert state(box)["state"] == "done"


def test_an_update_left_before_it_started_changed_nothing(box):
    (box.data / "updater").mkdir()
    (box.data / "updater" / "state.json").write_text(json.dumps(
        {"state": "updating", "from_version": OLD, "to_version": NEW}))
    updater(box).run()
    assert state(box)["state"] == "failed"
    assert "nothing changed" in state(box)["message"]
    assert box.updates == []


def test_a_queued_update_whose_request_vanished_says_so(box):
    (box.data / "updater").mkdir()
    (box.data / "updater" / "state.json").write_text(json.dumps({"state": "queued", "to_version": NEW}))
    updater(box).run()
    assert state(box)["state"] == "failed"


# -- a data folder the app can write --


def test_an_updater_folder_that_is_a_symlink_is_refused(box, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (box.data / "updater").symlink_to(elsewhere)
    with pytest.raises(OSError):
        updater(box).run()
    assert list(elsewhere.iterdir()) == []


def test_files_planted_as_symlinks_are_neither_read_nor_written_through(box, tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"version": NEW}))
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    folder = box.data / "updater"
    folder.mkdir()
    (folder / "request.json").symlink_to(secret)
    (folder / "heartbeat.json").symlink_to(victim)

    updater(box).run()

    assert box.updates == []  # the planted request wasn't followed
    assert victim.read_text() == "untouched"  # the heartbeat replaced the link, not its target
    assert not (folder / "heartbeat.json").is_symlink()


def test_an_oversized_request_is_ignored(box):
    folder = box.data / "updater"
    folder.mkdir()
    (folder / "request.json").write_text(json.dumps({"version": NEW, "pad": "x" * host.MAX_FILE_BYTES}))
    updater(box).run()
    assert box.updates == []


# -- installing --


@pytest.fixture
def as_root(monkeypatch):
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    monkeypatch.setattr(host, "_owned_by_root_only", lambda path: True)


def test_install_creates_the_cron_job_once(box, tmp_path, as_root):
    folder = tmp_path / "updater-home"
    folder.mkdir()
    script = folder / "obsidian-updater.py"
    script.write_text("")
    notes = host.install(box, "obsidian", script)
    assert "created" in notes[-1]
    host.install(box, "obsidian", script)
    (job,) = box.cron
    assert job["user"] == "root"
    assert job["command"] == f"/usr/bin/python3 {script.resolve()} run --app obsidian"
    assert job["schedule"]["minute"] == "*" and job["stdout"] and job["stderr"]
    assert oct(script.stat().st_mode & 0o777) == "0o700"


def test_install_runs_once_so_the_heartbeat_is_there_straight_away(box, tmp_path, as_root, monkeypatch, capsys):
    folder = tmp_path / "updater-home"
    folder.mkdir()
    script = folder / "obsidian-updater.py"
    script.write_text("")
    monkeypatch.setattr(host, "__file__", str(script))
    monkeypatch.setattr(host, "Middleware", lambda: box)

    assert host.main(["install", "--app", "obsidian"]) == 0

    out = capsys.readouterr().out
    assert "First run done." in out
    assert json.loads(out[out.index("{"):])["heartbeat"]["script"] == host.SCRIPT_VERSION
    assert len(box.cron) == 1


def test_install_refuses_a_folder_the_app_can_write(box, as_root):
    script = box.data / "obsidian-updater.py"
    script.write_text("")
    with pytest.raises(SystemExit, match="overlaps"):
        host.install(box, "obsidian", script)
    assert box.cron == []


def test_install_refuses_a_folder_holding_the_app_s(box, tmp_path, as_root):
    script = tmp_path / "obsidian-updater.py"  # tmp_path holds the app's data folder
    script.write_text("")
    with pytest.raises(SystemExit, match="overlaps"):
        host.install(box, "obsidian", script)


def test_install_needs_root(box, tmp_path, monkeypatch):
    monkeypatch.setattr(host.os, "geteuid", lambda: 501)
    with pytest.raises(SystemExit, match="root"):
        host.install(box, "obsidian", tmp_path / "x.py")


def test_the_script_runs_on_the_standard_library_alone():
    """Only TrueNAS's own client is allowed besides, imported where it's used."""
    tree = ast.parse(SCRIPT.read_text())
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top_level.add(node.module.split(".")[0])
    assert top_level - set(sys.stdlib_module_names) == set()
