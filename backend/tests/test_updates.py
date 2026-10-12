"""app/updates.py: versions, the registry lookup, release notes, and the
database copy taken before an update and restored after a rollback."""

import sqlite3

import pytest
import requests

from app import updates
from app.core import config, version
from app.db import RequestStore


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    return tmp_path


# -- versions --


def test_versions_compare_as_numbers_not_text():
    assert updates.is_newer("1.0.10", "1.0.9")
    assert updates.is_newer("1.1.0", "1.0.46")
    assert not updates.is_newer("1.0.46", "1.0.46")
    assert not updates.is_newer("1.0.45", "1.0.46")


def test_a_channel_name_is_not_a_version():
    assert updates.parse_version("stable") is None
    assert not updates.is_newer("beta", "1.0.0")
    assert not updates.is_newer("1.2.0", None)


def test_the_channel_is_the_one_the_running_image_was_published_on(monkeypatch):
    monkeypatch.setattr(config, "UPDATE_CHANNEL", None)
    monkeypatch.setattr(version, "current", lambda: version.Version("1.0.46", "5fd5677", None, "beta"))
    assert updates.channel() == "beta"
    monkeypatch.setattr(config, "UPDATE_CHANNEL", "stable")
    assert updates.channel() == "stable"


# -- the registry --


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))


def fake_registry(monkeypatch, tags, *, needs_token=True):
    """GHCR as it answers: a pull token first, then an index per tag
    pointing at an x86-64 image and an attestation, then its config."""
    calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        calls.append(url)
        if url.startswith("https://ghcr.io/token"):
            return FakeResponse(body={"token": "anon"})
        if needs_token and self.headers.get("Authorization") != "Bearer anon":
            return FakeResponse(401, headers={
                "WWW-Authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:x:pull"'
            })
        path = url.split("/v2/thyglizzyglobber/", 1)[1]
        image, _, rest = path.partition("/")
        kind, _, ref = rest.partition("/")
        if kind == "manifests" and ref in tags:
            return FakeResponse(body={"manifests": [
                {"digest": f"sha256:{ref}-amd64", "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": "sha256:att", "platform": {"os": "unknown", "architecture": "unknown"}},
            ]})
        if kind == "manifests" and ref.endswith("-amd64"):
            return FakeResponse(body={"config": {"digest": f"sha256:cfg-{ref}"}})
        if kind == "blobs":
            tag = ref.removeprefix("sha256:cfg-sha256:").removesuffix("-amd64")
            return FakeResponse(body={"config": {"Labels": {"org.opencontainers.image.version": tags[tag]}}})
        return FakeResponse(404)

    monkeypatch.setattr(requests.Session, "get", get)
    return calls


def test_the_version_on_a_channel_is_read_from_its_image(monkeypatch):
    calls = fake_registry(monkeypatch, {"beta": "1.0.47", "1.0.47": "1.0.47"})
    assert updates.published_version("beta") == "1.0.47"
    assert calls[1].startswith("https://ghcr.io/token")  # asked for a pull token after the 401


def test_a_missing_tag_is_none_not_an_error(monkeypatch):
    fake_registry(monkeypatch, {"beta": "1.0.47"})
    assert updates.published_version("stable") is None


def test_a_release_exists_only_when_every_image_has_it(monkeypatch):
    fake_registry(monkeypatch, {"1.0.47": "1.0.47"})
    assert updates.release_exists("1.0.47")
    assert not updates.release_exists("1.0.48")


def test_an_unreachable_registry_is_a_registry_error(monkeypatch):
    def offline(*_args, **_kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests.Session, "get", offline)
    with pytest.raises(updates.RegistryError, match="couldn't reach"):
        updates.published_version("stable")


# -- release notes --

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Not out yet.

## [1.1.0] - 2026-10-20

### Added

- **Release images.** Every push publishes
  images, wrapped onto a second line.
- In-app updates with `rollback`.

### Fixed

- A fix.

## [1.0.0] - 2026-09-28

- The first one.
"""


def test_notes_are_that_version_s_section_in_plain_text():
    assert updates.parse_release_notes(CHANGELOG, "1.1.0") == [
        {"heading": "Added", "items": ["Release images. Every push publishes images, wrapped onto a second line.",
                                       "In-app updates with rollback."]},
        {"heading": "Fixed", "items": ["A fix."]},
    ]


def test_bullets_without_a_heading_still_count():
    assert updates.parse_release_notes(CHANGELOG, "1.0.0") == [{"heading": "Changes", "items": ["The first one."]}]


def test_a_version_without_a_section_has_no_notes():
    assert updates.parse_release_notes(CHANGELOG, "1.0.46") == []


# -- shared files --


def test_a_request_is_left_for_the_updater_and_the_update_shows_queued(data):
    state = updates.request_update("1.0.47", "1.0.46", "obsidian-x.db")
    assert updates.read_json(updates.request_path()) == {
        "version": "1.0.47", "backup": "obsidian-x.db", "requested_at": state["started_at"],
    }
    assert updates.updater_state()["state"] == "queued"
    assert "queued" in updates.BUSY


def test_the_updater_counts_as_running_only_with_a_recent_heartbeat(data):
    assert not updates.updater_running()
    updates.write_json(updates.heartbeat_path(), {"at": updates.now_iso(), "script": 1})
    assert updates.updater_running()
    updates.write_json(updates.heartbeat_path(), {"at": "2026-01-01T00:00:00+00:00"})
    assert not updates.updater_running()
    updates.write_json(updates.heartbeat_path(), {"at": "not a time"})
    assert not updates.updater_running()


def test_json_files_are_written_whole(data):
    updates.write_json(updates.state_path(), {"state": "verifying"})
    assert updates.read_json(updates.state_path()) == {"state": "verifying"}
    assert [p.name for p in updates.updater_dir().iterdir()] == ["state.json"]
    assert updates.read_json(updates.updater_dir() / "missing.json") is None


# -- database copies --


def _store_with(path, title):
    store = RequestStore(path)
    store.create_request(1, title, 2020, None)
    return store


def _titles(path):
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT title FROM requests")]
    finally:
        conn.close()


def test_a_backup_is_a_working_copy_and_old_ones_are_pruned(data, monkeypatch):
    monkeypatch.setattr(config, "UPDATE_BACKUPS_KEPT", 2)
    store = _store_with(config.DB_PATH, "Heat")
    made = []
    for n in range(3):
        backup = updates.backup_database(store, "1.0.46", f"1.0.{47 + n}")
        made.append(backup.name)  # distinct within one second: the target differs
    assert _titles(updates.backups_dir() / made[-1]) == ["Heat"]
    assert sorted(p.name for p in updates.backups_dir().iterdir()) == sorted(made)[-2:]
    store.close()


def test_a_rollback_note_restores_the_copy_for_that_version_only(data):
    store = _store_with(config.DB_PATH, "Heat")
    backup = updates.backup_database(store, "1.0.46", "1.0.47")
    store.create_request(2, "Ronin", 1998, None)  # made after the copy, under the bad release
    store.close()
    updates.write_json(updates.restore_path(), {"backup": backup.name, "version": "1.0.46"})

    # The new release, still restarting before the rollback replaces it, leaves it alone.
    assert updates.restore_pending_backup(config.DB_PATH, "1.0.47") is None
    assert _titles(config.DB_PATH) == ["Heat", "Ronin"]

    assert updates.restore_pending_backup(config.DB_PATH, "1.0.46") == backup.name
    assert _titles(config.DB_PATH) == ["Heat"]
    assert not updates.restore_path().exists()
    assert list(data.glob("app.db.before-restore-*"))  # what was replaced is kept aside


def test_a_copy_that_wont_open_is_never_restored(data):
    RequestStore(config.DB_PATH).close()
    updates.backups_dir().mkdir(parents=True)
    (updates.backups_dir() / "obsidian-bad.db").write_text("not a database")
    updates.write_json(updates.restore_path(), {"backup": "obsidian-bad.db", "version": "1.0.46"})

    assert updates.restore_pending_backup(config.DB_PATH, "1.0.46") is None
    assert not updates.restore_path().exists()
    RequestStore(config.DB_PATH).close()  # the original still opens


def test_a_note_naming_a_missing_copy_is_dropped(data):
    updates.write_json(updates.restore_path(), {"backup": "gone.db", "version": "1.0.46"})
    assert updates.restore_pending_backup(config.DB_PATH, "1.0.46") is None
    assert not updates.restore_path().exists()
