"""Version numbers come from the release tag plus one patch per commit
since it — see app/version.py."""

from app import version


def test_the_tagged_commit_is_the_release_itself():
    assert version.parse_describe("v1.0.0-0-g1a2b3c4") == "1.0.0"


def test_each_commit_after_a_release_is_the_next_patch():
    assert version.parse_describe("v1.0.0-1-g1a2b3c4") == "1.0.1"
    assert version.parse_describe("v1.0.0-12-g1a2b3c4") == "1.0.12"


def test_a_later_release_tag_restarts_the_count():
    assert version.parse_describe("v1.1.0-3-g1a2b3c4") == "1.1.3"
    assert version.parse_describe("v2.0.0-0-g1a2b3c4") == "2.0.0"


def test_a_tag_that_is_not_a_release_gives_no_version():
    assert version.parse_describe("nightly-4-g1a2b3c4") is None
    assert version.parse_describe("") is None


def test_app_version_in_the_env_wins(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.2.3")
    monkeypatch.setattr(version, "_cached", None)

    assert version.current().version == "1.2.3"
    monkeypatch.setattr(version, "_cached", None)


def test_a_release_image_reports_what_ci_baked_in(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.1.4")
    monkeypatch.setenv("APP_COMMIT", "1a2b3c4")
    monkeypatch.setenv("APP_BUILD_DATE", "2026-10-11")
    monkeypatch.setenv("APP_CHANNEL", "beta")
    monkeypatch.setattr(version, "_cached", None)

    assert version.current() == version.Version("1.1.4", "1a2b3c4", "2026-10-11", "beta")
    monkeypatch.setattr(version, "_cached", None)


def test_a_git_clone_has_no_channel(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("APP_CHANNEL", raising=False)
    monkeypatch.setattr(version, "_cached", None)

    assert version.current().channel is None
    monkeypatch.setattr(version, "_cached", None)
