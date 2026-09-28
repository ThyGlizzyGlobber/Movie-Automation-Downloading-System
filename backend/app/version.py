"""The running build's version, read from git: the latest `vX.Y.Z` release
tag, with the patch number advanced by one for every commit since it. So
the commit tagged v1.0.0 is 1.0.0, the next one on main is 1.0.1, and so
on until the next tag (v1.1.0, say) starts the count again. Nothing is
bumped by hand; cutting a release is `git tag -a vX.Y.0` (see README).

The deployed clone is mounted at DEPLOY_REPO_PATH (/repo on the NAS).
That is the one to ask: the backend's own source is mounted separately at
/app/app, which is not a git checkout. A workstation has no /repo, so the
source tree's own repository answers there instead.

APP_VERSION in the environment overrides all of this, for an image built
without its .git."""

import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app import config

_DESCRIBE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-(\d+)-g([0-9a-f]+)$")
_CACHE_SECONDS = 60


@dataclass(frozen=True)
class Version:
    version: str | None  # "1.0.3"; None when git can't say
    commit: str | None  # short hash of the running commit
    date: str | None  # that commit's date, YYYY-MM-DD


def parse_describe(described: str) -> str | None:
    """`git describe --long` output -> "major.minor.(patch + commits since)".
    None for anything not cut from a vX.Y.Z tag."""
    match = _DESCRIBE.match(described.strip())
    if not match:
        return None
    major, minor, patch, since = (int(match.group(i)) for i in range(1, 5))
    return f"{major}.{minor}.{patch + since}"


def _repo() -> Path:
    deployed = Path(config.DEPLOY_REPO_PATH)
    if (deployed / ".git").exists():
        return deployed
    return Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=_repo(), capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _read() -> Version:
    override = os.environ.get("APP_VERSION")
    if override:
        return Version(override.lstrip("v"), None, None)
    described = _git("describe", "--tags", "--long", "--match", "v[0-9]*", "--abbrev=7")
    return Version(
        parse_describe(described) if described else None,
        _git("rev-parse", "--short=7", "HEAD"),
        _git("log", "-1", "--format=%cs"),
    )


# A pull that only changes the frontend doesn't restart the backend, so the
# answer is re-read now and then rather than once per process.
_cached: tuple[float, Version] | None = None


def current() -> Version:
    global _cached
    now = time.monotonic()
    if _cached is None or now - _cached[0] > _CACHE_SECONDS:
        _cached = (now, _read())
    return _cached[1]


def as_dict() -> dict:
    return asdict(current())
