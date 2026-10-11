"""app/updater.py's state machine, with TrueNAS and the two services faked:
an update that comes up, one that doesn't and is rolled back, one TrueNAS
refuses, and an updater that starts up partway through someone else's."""

import pytest
from fastapi.testclient import TestClient

from app import config, updater, updates
from app.truenas import TrueNASError

OLD, NEW = "1.0.46", "1.0.47"


def compose(tag):
    return {
        "services": {
            "frontend": {"image": f"ghcr.io/thyglizzyglobber/obsidian-frontend:{tag}"},
            "backend": {"image": f"ghcr.io/thyglizzyglobber/obsidian-backend:{tag}"},
            "updater": {"image": f"ghcr.io/thyglizzyglobber/obsidian-backend:{tag}"},
            "cloudflared": {"image": "cloudflare/cloudflared:latest"},
        }
    }


class Box:
    """TrueNAS plus what's running: an app.update moves `running` to the
    version the backend's image names, unless that version is broken."""

    def __init__(self, tag="stable"):
        self.compose = compose(tag)
        self.running = OLD
        self.broken: set[str] = set()
        self.refuse: str | None = None
        self.unreachable = False
        self.updates: list[dict] = []
        self.jobs: dict[int, dict] = {}

    def client(self):
        box = self

        class Client:
            def __enter__(self):
                if box.unreachable:
                    raise TrueNASError("couldn't reach TrueNAS")
                return self

            def __exit__(self, *exc):
                pass

            def app_config(self, name):
                assert name == config.TRUENAS_APP_NAME
                return box.compose

            def start_app_update(self, name, moved):
                job_id = len(box.jobs) + 1
                box.updates.append(moved)
                if box.refuse:
                    box.jobs[job_id] = {"state": "FAILED", "error": box.refuse}
                else:
                    box.compose = moved
                    tag = moved["services"]["backend"]["image"].rsplit(":", 1)[1]
                    box.running = OLD if tag == "stable" else tag  # :stable is what was installed
                    box.jobs[job_id] = {"state": "SUCCESS", "error": None}
                return job_id

            def wait_for_job(self, job_id, timeout):
                return box.jobs[job_id]

        return Client()

    def backend_version(self, _url):
        return None if self.running in self.broken else self.running


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "TRUENAS_API_USER", "obsidian-updater")
    monkeypatch.setattr(config, "TRUENAS_API_KEY", "1-key")
    monkeypatch.setattr(config, "UPDATE_VERIFY_SECONDS", 1)
    monkeypatch.setattr(updater.time, "sleep", lambda _s: None)
    the_box = Box()
    monkeypatch.setattr(updater, "TrueNASClient", the_box.client)
    monkeypatch.setattr(updater, "service_version", the_box.backend_version)
    monkeypatch.setattr(updater, "frontend_up", lambda: the_box.running not in the_box.broken)
    monkeypatch.setattr(updater, "running_version", lambda: the_box.running)
    return the_box


def start(target=NEW, backup="obsidian-x.db"):
    updates.write_json(updates.state_path(), {
        "state": "updating", "from_version": OLD, "to_version": target, "backup": backup,
    })
    updater.apply(target)
    return updater.read_state()


def test_retagging_moves_only_our_images():
    moved, before = updater.retag(compose("stable"), NEW)
    assert moved["services"]["backend"]["image"] == f"ghcr.io/thyglizzyglobber/obsidian-backend:{NEW}"
    assert moved["services"]["updater"]["image"] == f"ghcr.io/thyglizzyglobber/obsidian-backend:{NEW}"
    assert moved["services"]["cloudflared"]["image"] == "cloudflare/cloudflared:latest"
    assert set(before) == {"frontend", "backend", "updater"}
    assert compose("stable")["services"]["backend"]["image"].endswith(":stable")  # the original is untouched


def test_an_update_that_comes_up_is_done(box):
    state = start()
    assert state["state"] == "done"
    assert box.running == NEW
    assert state["previous_images"]["backend"].endswith(":stable")
    assert not updates.restore_path().exists()


def test_an_update_that_never_comes_up_is_rolled_back_with_its_data(box):
    box.broken.add(NEW)
    state = start()
    assert state["state"] == "rolled_back"
    assert box.running == OLD
    assert [u["services"]["backend"]["image"].rsplit(":", 1)[1] for u in box.updates] == [NEW, "stable"]
    assert "went back" in state["message"]
    # The old backend restores the copy as it boots, and only it.
    assert updates.read_json(updates.restore_path()) == {"backup": "obsidian-x.db", "version": OLD}


def test_a_refused_update_with_the_old_release_still_up_changes_nothing(box):
    box.refuse = "image pull failed"
    state = start()
    assert state["state"] == "failed"
    assert "nothing changed" in state["message"] and "image pull failed" in state["message"]
    assert len(box.updates) == 1  # no rollback attempted: there was nothing to undo


def test_truenas_unreachable_is_a_plain_failure(box):
    box.unreachable = True
    state = start()
    assert state["state"] == "failed"
    assert box.updates == []


def test_an_app_without_our_images_is_left_alone(box):
    box.compose = {"services": {"web": {"image": "nginx:alpine"}}}
    state = start()
    assert state["state"] == "failed"
    assert box.updates == []


def test_the_replacement_updater_picks_up_and_verifies(box):
    # The usual case: the update replaced the updater mid-wait, so the
    # new one (already running NEW) finds "updating" in the state file.
    box.running = NEW
    updates.write_json(updates.state_path(), {"state": "updating", "from_version": OLD, "to_version": NEW})
    updater.resume()
    assert updater.read_state()["state"] == "done"


def test_the_restored_updater_confirms_a_rollback(box):
    box.running = OLD
    updates.write_json(updates.state_path(), {
        "state": "rolling_back", "from_version": OLD, "to_version": NEW, "message": "It didn't start",
    })
    updater.resume()
    state = updater.read_state()
    assert state["state"] == "rolled_back"
    assert state["message"] == f"It didn't start, so Obsidian went back to {OLD}."


# -- the API the backend calls --


@pytest.fixture
def api(box, monkeypatch):
    monkeypatch.setattr(updates, "release_exists", lambda v: v in (NEW, "1.0.48"))
    started = []
    monkeypatch.setattr(updater, "_in_background", lambda work, *args: started.append(args))
    with TestClient(updater.app) as client:
        yield client, updates.shared_token(), started


def test_every_call_needs_the_backend_s_token(api):
    client, token, _ = api
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"X-Updater-Token": "guess"}).status_code == 401
    answer = client.get("/status", headers={"X-Updater-Token": token})
    assert answer.status_code == 200
    assert answer.json()["configured"] is True
    assert client.get("/health").status_code == 200  # the container's own healthcheck needs none


def test_an_update_starts_and_says_so(api):
    client, token, started = api
    answer = client.post("/update", json={"version": NEW, "from_version": OLD, "backup": "b.db"},
                         headers={"X-Updater-Token": token})
    assert answer.status_code == 202
    assert answer.json()["state"] == "updating"
    assert started == [(NEW,)]


def test_only_a_published_version_and_one_at_a_time(api):
    client, token, _ = api
    headers = {"X-Updater-Token": token}
    assert client.post("/update", json={"version": "stable", "from_version": OLD}, headers=headers).status_code == 422
    assert client.post("/update", json={"version": "9.9.9", "from_version": OLD}, headers=headers).status_code == 404
    assert client.post("/update", json={"version": NEW, "from_version": OLD}, headers=headers).status_code == 202
    assert client.post("/update", json={"version": "1.0.48", "from_version": OLD}, headers=headers).status_code == 409


def test_no_key_set_up_refuses_rather_than_failing_later(api, monkeypatch):
    client, token, _ = api
    monkeypatch.setattr(config, "TRUENAS_API_KEY", None)
    answer = client.post("/update", json={"version": NEW, "from_version": OLD}, headers={"X-Updater-Token": token})
    assert answer.status_code == 409


def test_starting_up_resumes_an_unfinished_update(box, monkeypatch):
    updates.write_json(updates.state_path(), {"state": "verifying", "from_version": OLD, "to_version": NEW})
    resumed = []
    monkeypatch.setattr(updater, "_in_background", lambda work, *args: resumed.append(work))
    with TestClient(updater.app):
        pass
    assert resumed == [updater.resume]

