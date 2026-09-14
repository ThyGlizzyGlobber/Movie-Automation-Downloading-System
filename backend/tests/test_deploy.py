"""app/deploy.py against real, local, offline git repos — file:// remotes,
no network. Covers the actual git plumbing `POST /api/admin/deploy` runs;
see test_api.py's deploy tests for route wiring/error-mapping instead."""

import subprocess

import pytest

from app import config, deploy


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


# A minimal, zero-dependency package.json + matching lockfile — real
# enough for `npm ci` to succeed fully offline (no registry access, no
# real deps to resolve) while still exercising the actual `npm ci`/
# `npm run build` subprocess calls deploy.py runs, not a mock of them.
# The build script itself doesn't need Vite (or any dependency) to prove
# the plumbing works — it just has to write *something* to dist/.
_FRONTEND_PACKAGE_JSON = """\
{
  "name": "test-frontend",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "build": "mkdir -p dist && echo ok > dist/marker.txt"
  }
}
"""
_FRONTEND_PACKAGE_LOCK = """\
{
  "name": "test-frontend",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "test-frontend",
      "version": "1.0.0"
    }
  }
}
"""


def _write_frontend_package(seed_frontend_dir, build_script="mkdir -p dist && echo ok > dist/marker.txt"):
    seed_frontend_dir.mkdir(parents=True, exist_ok=True)
    (seed_frontend_dir / "package.json").write_text(
        _FRONTEND_PACKAGE_JSON.replace(
            '"build": "mkdir -p dist && echo ok > dist/marker.txt"', f'"build": "{build_script}"'
        )
    )
    (seed_frontend_dir / "package-lock.json").write_text(_FRONTEND_PACKAGE_LOCK)


@pytest.fixture
def repo_pair(tmp_path, monkeypatch):
    """A bare 'origin' plus a clone of it — the clone stands in for the
    deployed-copy mount deploy.py operates on. Seeded with a real (if
    trivial) frontend/ package so run_git_pull()'s own `npm ci`/
    `npm run build` steps have something real to run against, matching
    every other test in this file's "real git, no mocks" style."""
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    _run("git", "init", "--bare", str(origin), cwd=tmp_path)

    seed = tmp_path / "seed"
    _run("git", "init", str(seed), cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.com", cwd=seed)
    _run("git", "config", "user.name", "Test", cwd=seed)
    (seed / "file.txt").write_text("v1\n")
    _write_frontend_package(seed / "frontend")
    _run("git", "add", "file.txt", "frontend", cwd=seed)
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


# ---------------------------------------------------------------------------
# Frontend migration Part B — `npm ci` + `npm run build`, run in the
# clone's own frontend/ directory after every successful pull.
# ---------------------------------------------------------------------------


def test_run_git_pull_builds_the_frontend(repo_pair):
    _origin, clone, _seed = repo_pair

    deploy.run_git_pull()

    assert (clone / "frontend" / "dist" / "marker.txt").read_text().strip() == "ok"


def test_run_git_pull_rebuilds_on_every_pull_even_when_already_up_to_date(repo_pair):
    """Not just "when something changed" — a prior build could have
    failed or been interrupted, so every successful pull re-runs it,
    "already up to date" included."""
    _origin, clone, _seed = repo_pair
    deploy.run_git_pull()
    marker = clone / "frontend" / "dist" / "marker.txt"
    marker.unlink()

    deploy.run_git_pull()

    assert marker.read_text().strip() == "ok"


def test_run_git_pull_raises_when_the_frontend_build_script_fails(repo_pair, monkeypatch):
    origin, clone, seed = repo_pair
    _write_frontend_package(seed / "frontend", build_script="exit 1")
    _run("git", "add", "frontend", cwd=seed)
    _run("git", "commit", "-m", "break the build", cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)

    with pytest.raises(deploy.DeployError):
        deploy.run_git_pull()
    # The git pull itself must still have succeeded and be reflected in
    # the clone — only the subsequent build step failed.
    assert (clone / "frontend" / "package.json").read_text() == (seed / "frontend" / "package.json").read_text()


def test_run_git_pull_frontend_build_error_surfaces_npm_output(repo_pair):
    origin, clone, seed = repo_pair
    _write_frontend_package(seed / "frontend", build_script="echo 'boom, something broke' >&2 && exit 1")
    _run("git", "add", "frontend", cwd=seed)
    _run("git", "commit", "-m", "break the build with a message", cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)

    with pytest.raises(deploy.DeployError, match="boom, something broke"):
        deploy.run_git_pull()


def test_run_git_pull_raises_when_npm_is_not_on_path(repo_pair, monkeypatch):
    """Simulates the real failure mode a backend image that hasn't been
    rebuilt to bake in Node would hit — git itself still has to work
    (it's what got the new frontend/package.json here in the first
    place), only npm is missing, so this can't just blank out PATH
    wholesale the way the git-focused tests in this file do."""
    _origin, _clone, _seed = repo_pair
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if args[0] == "npm":
            raise FileNotFoundError(2, "No such file or directory", "npm")
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(deploy.DeployError, match="npm"):
        deploy.run_git_pull()
