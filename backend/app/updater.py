"""The updater sidecar: the one container holding a TrueNAS API key, and the
only thing that changes which version of Obsidian a box runs.

It runs from the backend's own image with a different command
(`uvicorn app.updater:app --port 8001`), so it moves version in lockstep
with the release it manages. It has no published port; the backend reaches
it on the app's network, showing the token from the shared data volume.

An update replaces this container too, partway through. So every step is
written to the state file before it's taken, and a starting updater picks
up whatever its predecessor left unfinished:

  idle -> updating -> verifying -> done
                         |
                         +-> rolling_back -> rolled_back
  (or failed, at any point nothing could be done about)

  updating      the app's images are being moved to the new version
  verifying     waiting for that version to come up healthy
  rolling_back  it didn't, so the previous images are going back, and the
                old backend has been asked to restore the database copy
"""

import copy
import logging
import threading
import time
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app import config, updates, version
from app.logging_config import configure_logging
from app.truenas import TrueNASClient, TrueNASError, job_error

configure_logging()
logger = logging.getLogger("app.updater")

BUSY = ("updating", "verifying", "rolling_back")
# How long TrueNAS gets to pull and restart the app before the updater
# stops waiting on the job itself (it keeps checking health afterwards).
JOB_TIMEOUT_SECONDS = 1800

_lock = threading.Lock()


def read_state() -> dict:
    return updates.read_json(updates.state_path()) or {"state": "idle"}


def write_state(**changes) -> dict:
    state = {**read_state(), **changes, "updated_at": updates.now_iso()}
    updates.write_json(updates.state_path(), state)
    return state


def finish(state: str, message: str) -> None:
    logger.info("update %s: %s", state, message)
    write_state(state=state, message=message, finished_at=updates.now_iso())


def configured() -> bool:
    return bool(config.TRUENAS_API_USER and config.TRUENAS_API_KEY)


def running_version() -> str | None:
    return version.current().version


# ---------------------------------------------------------------------------
# Moving the app between versions
# ---------------------------------------------------------------------------


def retag(compose: dict, tag: str) -> tuple[dict, dict[str, str]]:
    """A copy of the app's compose config with every one of our images moved
    to `tag`, and what each of those services ran before."""
    moved = copy.deepcopy(compose)
    before: dict[str, str] = {}
    for name, service in (moved.get("services") or {}).items():
        image = (service or {}).get("image") or ""
        ours = updates.is_our_image(image)
        if ours:
            before[name] = image
            service["image"] = updates.image_ref(ours, tag)
    return moved, before


def restore_images(compose: dict, images: dict[str, str]) -> dict:
    moved = copy.deepcopy(compose)
    for name, image in images.items():
        if name in (moved.get("services") or {}):
            moved["services"][name]["image"] = image
    return moved


def apply(target: str) -> None:
    """Moves the app to `target` and waits on TrueNAS. Usually this process
    is replaced partway through, and its successor verifies (see resume)."""
    try:
        with TrueNASClient() as truenas:
            compose = truenas.app_config(config.TRUENAS_APP_NAME)
            moved, before = retag(compose, target)
            if not before:
                finish("failed", f"Couldn't find Obsidian's images in the TrueNAS app {config.TRUENAS_APP_NAME!r}.")
                return
            write_state(previous_images=before)
            job_id = truenas.start_app_update(config.TRUENAS_APP_NAME, moved)
            write_state(job_id=job_id)
            logger.info("TrueNAS job %s is moving %s to %s", job_id, config.TRUENAS_APP_NAME, target)
            job = truenas.wait_for_job(job_id, JOB_TIMEOUT_SECONDS)
    except TrueNASError as exc:
        after_failed_job(str(exc))
        return
    if job.get("state") == "SUCCESS":
        verify()
    else:
        after_failed_job(job_error(job))


def after_failed_job(reason: str) -> None:
    """TrueNAS couldn't finish the move. If the old release is still up and
    answering, nothing changed and there's nothing to undo; otherwise the
    app may be half-moved, so put it back."""
    state = read_state()
    if service_version(config.BACKEND_URL) == state.get("from_version") and frontend_up():
        finish("failed", f"The update couldn't be installed, so nothing changed: {reason}")
    else:
        roll_back(f"The update couldn't be installed ({reason})")


def service_version(base_url: str) -> str | None:
    try:
        response = requests.get(f"{base_url}/api/health", timeout=5)
        return response.json().get("version") if response.ok else None
    except (requests.RequestException, ValueError):
        return None


def frontend_up() -> bool:
    try:
        return requests.get(config.FRONTEND_URL, timeout=5).ok
    except requests.RequestException:
        return False


def verify() -> None:
    """Waits for the backend to answer as the new version, with the
    frontend serving, within UPDATE_VERIFY_SECONDS. Otherwise rolls back."""
    state = write_state(state="verifying")
    target = state.get("to_version")
    deadline = time.monotonic() + config.UPDATE_VERIFY_SECONDS
    while time.monotonic() < deadline:
        if service_version(config.BACKEND_URL) == target and frontend_up():
            finish("done", f"Updated to {target}.")
            return
        time.sleep(3)
    minutes = max(1, round(config.UPDATE_VERIFY_SECONDS / 60))
    roll_back(f"Version {target} didn't start properly within {minutes} minute{'' if minutes == 1 else 's'}")


def roll_back(reason: str) -> None:
    state = read_state()
    previous = state.get("previous_images")
    if not previous:
        finish("failed", f"{reason}, and there was nothing recorded to go back to.")
        return
    write_state(state="rolling_back", message=reason)
    # Before anything moves: the old backend reads this as it boots. Its
    # version check is what keeps the new one from acting on it meanwhile.
    if state.get("backup") and state.get("from_version"):
        updates.write_json(
            updates.restore_path(), {"backup": state["backup"], "version": state["from_version"]}
        )
    try:
        with TrueNASClient() as truenas:
            compose = truenas.app_config(config.TRUENAS_APP_NAME)
            job_id = truenas.start_app_update(config.TRUENAS_APP_NAME, restore_images(compose, previous))
            write_state(rollback_job_id=job_id)
            job = truenas.wait_for_job(job_id, JOB_TIMEOUT_SECONDS)
    except TrueNASError as exc:
        finish("failed", f"{reason}, and going back to {state.get('from_version')} failed too: {exc}")
        return
    if job.get("state") == "SUCCESS":
        confirm_rollback()
    else:
        finish("failed", f"{reason}, and going back to {state.get('from_version')} failed too: {job_error(job)}")


def confirm_rollback() -> None:
    state = read_state()
    previous = state.get("from_version")
    deadline = time.monotonic() + config.UPDATE_VERIFY_SECONDS
    while time.monotonic() < deadline:
        if service_version(config.BACKEND_URL) == previous and frontend_up():
            finish("rolled_back", f"{state.get('message')}, so Obsidian went back to {previous}.")
            return
        time.sleep(3)
    finish("failed", f"{state.get('message')}. Going back to {previous} didn't come up either.")


def resume() -> None:
    """Picks up an update the previous updater container didn't live to
    finish — the usual case, since the update replaces it."""
    state = read_state()
    current = running_version()
    if state.get("state") == "updating":
        if current == state.get("to_version"):
            verify()  # this updater is the new release, so the move happened
        elif state.get("job_id"):
            try:
                with TrueNASClient() as truenas:
                    job = truenas.wait_for_job(state["job_id"], JOB_TIMEOUT_SECONDS)
            except TrueNASError as exc:
                after_failed_job(str(exc))
                return
            if job.get("state") == "SUCCESS":
                verify()
            else:
                after_failed_job(job_error(job))
        else:
            after_failed_job("the updater restarted before TrueNAS started the update")
    elif state.get("state") == "verifying":
        verify()
    elif state.get("state") == "rolling_back":
        if current == state.get("from_version"):
            confirm_rollback()
        else:
            roll_back(state.get("message") or "The update didn't start properly")


def _in_background(work, *args) -> None:
    def run():
        with _lock:
            try:
                work(*args)
            except Exception:  # pragma: no cover — logged, and the state says so
                logger.exception("update step failed")
                finish("failed", "The updater hit an unexpected error; see its log.")

    threading.Thread(target=run, name="updater", daemon=True).start()


# ---------------------------------------------------------------------------
# The API the backend calls
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    updates.shared_token(create=True)
    if read_state().get("state") in BUSY:
        _in_background(resume)
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _check_token(token: str | None) -> None:
    expected = updates.shared_token()
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="not the backend")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/status")
def status(x_updater_token: str | None = Header(default=None)) -> dict:
    _check_token(x_updater_token)
    return {"configured": configured(), "running_version": running_version(), **read_state()}


class UpdateRequest(BaseModel):
    version: str
    from_version: str
    backup: str | None = None


@app.post("/update", status_code=202)
def start_update(body: UpdateRequest, x_updater_token: str | None = Header(default=None)) -> dict:
    _check_token(x_updater_token)
    if not configured():
        raise HTTPException(status_code=409, detail="no TrueNAS API key is set up for updates")
    if not updates.parse_version(body.version):
        raise HTTPException(status_code=422, detail="a version looks like 1.2.3")
    if _lock.locked() or read_state().get("state") in BUSY:
        raise HTTPException(status_code=409, detail="an update is already under way")
    try:
        if not updates.release_exists(body.version):
            raise HTTPException(status_code=404, detail=f"version {body.version} isn't published")
    except updates.RegistryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state = {
        "state": "updating",
        "from_version": body.from_version,
        "to_version": body.version,
        "backup": body.backup,
        "started_at": updates.now_iso(),
        "finished_at": None,
        "message": None,
        "previous_images": None,
        "job_id": None,
        "rollback_job_id": None,
        "updated_at": updates.now_iso(),
    }
    updates.write_json(updates.state_path(), state)
    _in_background(apply, body.version)
    return state
