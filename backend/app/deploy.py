"""Stage 6: the entire self-update surface. `run_git_pull()` is the whole
implementation of `POST /api/admin/deploy` — exactly `git pull --ff-only`
against the deployed-copy clone, no path/branch/command ever accepted as
input, per the confirmed architecture's "one fixed verb" deploy model.

`--ff-only` is deliberate, not the git default: the deployed-copy clone is
never committed to directly, so a non-fast-forward pull means something
unexpected happened upstream (force-push, manual edit on the NAS) — fail
loudly rather than silently create a merge commit nobody asked for. Same
"fail safe, not best guess" principle as the pipeline's own matching logic.
"""

import os
import subprocess

from app import config


class DeployError(Exception):
    """Raised for any git-pull failure — not-a-repo, network, non-ff, or
    timeout. api.py maps this straight to a 502."""


def _git(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if config.GIT_SSH_KEY_PATH:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {config.GIT_SSH_KEY_PATH} -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=accept-new"
        )
    return subprocess.run(
        ["git", *args],
        cwd=config.DEPLOY_REPO_PATH,
        env=env,
        capture_output=True,
        text=True,
        timeout=config.DEPLOY_TIMEOUT_SECONDS,
    )


def run_git_pull() -> dict:
    if not (config.DEPLOY_REPO_PATH / ".git").is_dir():
        raise DeployError(f"{config.DEPLOY_REPO_PATH} is not a git clone")

    try:
        result = _git("pull", "--ff-only")
    except subprocess.TimeoutExpired as exc:
        raise DeployError("git pull timed out") from exc
    if result.returncode != 0:
        raise DeployError((result.stderr or result.stdout or "git pull failed").strip())

    sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
    return {"detail": result.stdout.strip() or "Already up to date.", "commit": sha}
