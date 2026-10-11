"""truenas/switch-to-release.py: moving a git-pull install onto the release
images in place. The app here is shaped like the real one on the
development NAS (TrueNAS 25.10.7's app.config for movie-downloader)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "truenas" / "switch-to-release.py"
spec = importlib.util.spec_from_file_location("switch_to_release", SCRIPT)
switch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(switch)

REPO = "/mnt/SSD/AppData/movie-downloader/repo"


def git_pull_install():
    return {
        "services": {
            "frontend": {
                "build": {"context": f"{REPO}/frontend"},
                "ports": ["8095:80"],
                "volumes": [f"{REPO}/frontend/dist:/usr/share/nginx/html", "tmdb-cache:/var/cache/nginx/tmdb",
                            "/mnt/SSD/AppData/movie-downloader/data:/srv/backend-data:ro"],
                "depends_on": ["backend"],
                "restart": "unless-stopped",
            },
            "backend": {
                "build": {"context": f"{REPO}/backend"},
                "environment": {
                    "TRAILER_X_ACCEL_PREFIX": "/internal-trailers/", "TMDB_API_KEY": "secret-tmdb",
                    "QBIT_HOST": "192.168.0.133", "QBIT_PORT": "30024",
                    "DEPLOY_REPO_PATH": "/repo", "GIT_SSH_KEY_PATH": "/run/secrets/deploy_key",
                    "TV_LIBRARY_ROOT": "/tv-library", "MOVIE_LIBRARY_ROOT": "/movie-library",
                },
                "expose": ["8000"],
                "restart": "unless-stopped",
                "volumes": [f"{REPO}/backend/app:/app/app", f"{REPO}:/repo",
                            "/mnt/SSD/AppData/movie-downloader/secrets/deploy_key:/run/secrets/deploy_key:ro",
                            "/mnt/SSD/AppData/movie-downloader/data:/app/data",
                            "/mnt/Media/TV Shows:/tv-library", "/mnt/Media/Movies:/movie-library"],
            },
            "cloudflared": {
                "image": "cloudflare/cloudflared:latest", "command": "tunnel --no-autoupdate run",
                "environment": {"TUNNEL_TOKEN": "secret-tunnel"}, "depends_on": ["frontend"],
                "restart": "unless-stopped",
            },
        },
        "volumes": {"tmdb-cache": {}},
    }


def test_switching_changes_only_what_a_release_install_does_without():
    before = git_pull_install()
    after, changes = switch.convert(before, "beta")
    frontend, backend = after["services"]["frontend"], after["services"]["backend"]

    assert frontend["image"] == "ghcr.io/thyglizzyglobber/obsidian-frontend:beta" and "build" not in frontend
    assert backend["image"] == "ghcr.io/thyglizzyglobber/obsidian-backend:beta" and "build" not in backend
    assert frontend["volumes"] == ["tmdb-cache:/var/cache/nginx/tmdb",
                                   "/mnt/SSD/AppData/movie-downloader/data:/srv/backend-data:ro"]
    assert backend["volumes"] == ["/mnt/SSD/AppData/movie-downloader/data:/app/data",
                                  "/mnt/Media/TV Shows:/tv-library", "/mnt/Media/Movies:/movie-library"]
    assert "DEPLOY_REPO_PATH" not in backend["environment"] and "GIT_SSH_KEY_PATH" not in backend["environment"]
    assert backend["environment"]["TMDB_API_KEY"] == "secret-tmdb"  # carried over as is
    assert backend["environment"]["QBIT_HOST"] == "192.168.0.133"
    assert after["services"]["cloudflared"] == before["services"]["cloudflared"]
    assert after["volumes"] == before["volumes"]
    for kept in ("ports", "depends_on", "restart"):
        assert frontend[kept] == before["services"]["frontend"][kept]
    assert any("unchanged: cloudflared" in change for change in changes)
    assert before["services"]["backend"]["build"]  # the original is untouched


def test_list_form_settings_are_handled_too():
    before = git_pull_install()
    before["services"]["backend"]["environment"] = ["TMDB_API_KEY=x", "DEPLOY_REPO_PATH=/repo"]
    after, _ = switch.convert(before, "stable")
    assert after["services"]["backend"]["environment"] == ["TMDB_API_KEY=x"]


def test_the_summary_shows_setting_names_never_their_values():
    text = "\n".join(switch.describe(git_pull_install()) + switch.convert(git_pull_install(), "beta")[1])
    assert "TMDB_API_KEY" in text and "TUNNEL_TOKEN" in text
    assert "secret-tmdb" not in text and "secret-tunnel" not in text


def test_an_app_already_on_release_images_is_left_alone():
    switched, _ = switch.convert(git_pull_install(), "beta")
    with pytest.raises(SystemExit, match="already runs release images"):
        switch.convert(switched, "beta")


def test_something_that_isnt_obsidian_is_refused():
    with pytest.raises(SystemExit, match="no 'frontend' service"):
        switch.convert({"services": {"web": {"image": "nginx"}}}, "beta")


class FakeTrueNAS:
    def __init__(self, up_after_switch=True):
        self.compose = git_pull_install()
        self.updates = []
        self.up_after_switch = up_after_switch

    def app_config(self, app):
        return json.loads(json.dumps(self.compose))

    def app_update(self, app, compose):
        self.updates.append(compose)
        self.compose = compose


@pytest.fixture
def nas(monkeypatch, tmp_path):
    monkeypatch.setattr(switch.os, "geteuid", lambda: 0)
    monkeypatch.setattr(switch, "published", lambda image, channel, prefix=switch.IMAGE_PREFIX: True)
    monkeypatch.setattr(switch, "__file__", str(tmp_path / "switch-to-release.py"))
    fake = FakeTrueNAS()

    def site(base, seconds=switch.WAIT_SECONDS, sleep=None):
        on_release = "image" in fake.compose["services"]["backend"]
        return "1.0.49" if (fake.up_after_switch or not on_release) else None

    monkeypatch.setattr(switch, "wait_until_up", site)
    monkeypatch.setattr(switch, "running_version", lambda base: "1.0.46")
    return fake, tmp_path


def test_nothing_changes_without_saying_switch(nas, capsys):
    fake, _ = nas
    assert switch.main(["--app", "movie-downloader", "--channel", "beta"], fake, ask=lambda _p: "no") == 1
    assert fake.updates == []
    out = capsys.readouterr().out
    assert "secret-tmdb" not in out and "secret-tunnel" not in out


def test_a_switch_saves_the_old_config_first_and_applies(nas):
    fake, folder = nas
    assert switch.main(["--app", "movie-downloader", "--channel", "beta"], fake, ask=lambda _p: "switch") == 0
    (saved,) = folder.glob("before-switch-*.json")
    assert json.loads(saved.read_text()) == git_pull_install()
    assert oct(saved.stat().st_mode & 0o777) == "0o600"
    assert fake.compose["services"]["backend"]["image"].endswith("obsidian-backend:beta")


def test_a_switch_that_doesnt_come_up_is_put_back(nas):
    fake, _ = nas
    fake.up_after_switch = False
    assert switch.main(["--app", "movie-downloader", "--channel", "beta"], fake, ask=lambda _p: "switch") == 1
    assert len(fake.updates) == 2
    assert fake.compose == git_pull_install()


def test_revert_puts_a_saved_config_back(nas):
    fake, folder = nas
    switch.main(["--app", "movie-downloader", "--channel", "beta"], fake, ask=lambda _p: "switch")
    (saved,) = folder.glob("before-switch-*.json")
    assert switch.main(["--app", "movie-downloader", "--revert", str(saved)], fake, ask=lambda _p: "revert") == 0
    assert fake.compose == git_pull_install()


def test_an_unpublished_channel_changes_nothing(nas, monkeypatch):
    fake, _ = nas
    monkeypatch.setattr(switch, "published", lambda image, channel, prefix=switch.IMAGE_PREFIX: False)
    with pytest.raises(SystemExit, match="isn't published"):
        switch.main(["--app", "movie-downloader", "--channel", "stable"], fake, ask=lambda _p: "switch")
    assert fake.updates == []
