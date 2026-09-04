"""app/deploy.py against real, local, offline git repos — file:// remotes,
no network. Covers the actual git plumbing `POST /api/admin/deploy` runs;
see test_api.py's deploy tests for route wiring/error-mapping instead."""

import subprocess

import pytest

from app import config, deploy


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo_pair(tmp_path, monkeypatch):
    """A bare 'origin' plus a clone of it — the clone stands in for the
    deployed-copy mount deploy.py operates on."""
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    _run("git", "init", "--bare", str(origin), cwd=tmp_path)

    seed = tmp_path / "seed"
    _run("git", "init", str(seed), cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.com", cwd=seed)
    _run("git", "config", "user.name", "Test", cwd=seed)
    (seed / "file.txt").write_text("v1\n")
    _run("git", "add", "file.txt", cwd=seed)
    _run("git", "commit", "-m", "initial", cwd=seed)
    _run("git", "branch", "-M", "main", cwd=seed)
    _run("git", "remote", "add", "origin", str(origin), cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)

    _run("git", "clone", str(origin), str(clone), cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.com", cwd=clone)
    _run("git", "config", "user.name", "Test", cwd=clone)

    monkeypatch.setattr(config, "DEPLOY_REPO_PATH", clone)
    monkeypatch.setattr(config, "GIT_SSH_KEY_PATH", None)
    return origin, clone, seed


def test_run_git_pull_when_already_up_to_date(repo_pair):
    _origin, _clone, _seed = repo_pair

    result = deploy.run_git_pull()

    assert result["detail"] == "Already up to date."
    assert len(result["commit"]) >= 7


def test_run_git_pull_fetches_new_commit(repo_pair):
    origin, clone, seed = repo_pair
    before_sha = deploy.run_git_pull()["commit"]

    (seed / "file.txt").write_text("v2\n")
    _run("git", "add", "file.txt", cwd=seed)
    _run("git", "commit", "-m", "second", cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)

    result = deploy.run_git_pull()

    assert result["commit"] != before_sha
    assert (clone / "file.txt").read_text() == "v2\n"


def test_run_git_pull_raises_when_path_is_not_a_git_repo(tmp_path, monkeypatch):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    monkeypatch.setattr(config, "DEPLOY_REPO_PATH", not_a_repo)

    with pytest.raises(deploy.DeployError, match="not a git clone"):
        deploy.run_git_pull()


def test_run_git_pull_raises_on_non_fast_forward(repo_pair):
    origin, clone, seed = repo_pair

    # Diverge: a local commit on the clone that was never pushed, plus a
    # different upstream commit — a real non-ff scenario, not a mock.
    (clone / "file.txt").write_text("local-only\n")
    _run("git", "add", "file.txt", cwd=clone)
    _run("git", "commit", "-m", "local divergent commit", cwd=clone)

    (seed / "file.txt").write_text("v2\n")
    _run("git", "add", "file.txt", cwd=seed)
    _run("git", "commit", "-m", "second", cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)

    with pytest.raises(deploy.DeployError):
        deploy.run_git_pull()
