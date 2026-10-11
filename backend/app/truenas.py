"""A small client for TrueNAS's own API — JSON-RPC 2.0 over a WebSocket at
wss://<host>/api/current, as TrueNAS 25.04 and later serve it — with only
what the updater needs: sign in with an API key, read and update an app,
and follow the job an update runs as.

Always TLS. TrueNAS revokes a user's API key the moment it arrives over
plain HTTP, so there is deliberately no ws:// option here. The certificate
isn't verified by default (config.TRUENAS_VERIFY_TLS), because a box serves
the self-signed one TrueNAS made for itself.
"""

import itertools
import json
import ssl
import time
from contextlib import ExitStack
from pathlib import Path

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from app import config


class TrueNASError(Exception):
    pass


# Job states a job doesn't leave.
FINISHED = ("SUCCESS", "FAILED", "ABORTED")
CONNECT_ATTEMPTS = 3
CONNECT_RETRY_SECONDS = 2


def default_gateway(route_table: Path = Path("/proc/net/route")) -> str | None:
    """The container's default gateway, which on an app's bridge network is
    the NAS itself. Read from the kernel's route table, where the gateway
    is little-endian hex."""
    try:
        lines = route_table.read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000" and fields[2] != "00000000":
            raw = bytes.fromhex(fields[2])
            return ".".join(str(b) for b in reversed(raw))
    return None


class TrueNASClient:
    """Use as a context manager: connects and signs in on entry."""

    def __init__(
        self,
        host: str | None = None,
        username: str | None = None,
        api_key: str | None = None,
        verify_tls: bool | None = None,
        timeout: float = 30,
    ):
        self.host = host or config.TRUENAS_HOST or default_gateway()
        self.username = username or config.TRUENAS_API_USER
        self.api_key = api_key or config.TRUENAS_API_KEY
        self.verify_tls = config.TRUENAS_VERIFY_TLS if verify_tls is None else verify_tls
        self.timeout = timeout
        self._ids = itertools.count(1)
        self._ws = None
        self._stack = ExitStack()

    def __enter__(self) -> "TrueNASClient":
        if not self.host:
            raise TrueNASError("couldn't work out where TrueNAS is; set TRUENAS_HOST")
        if not (self.username and self.api_key):
            raise TrueNASError("no TrueNAS API key is set up for updates")
        context = ssl.create_default_context()
        if not self.verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        # A few tries: TrueNAS's middleware can be busy or restarting for a
        # moment, and one dropped handshake shouldn't fail an update.
        for attempt in range(CONNECT_ATTEMPTS):
            try:
                self._ws = self._stack.enter_context(
                    connect(
                        f"wss://{self.host}/api/current",
                        ssl=context,
                        open_timeout=self.timeout,
                        max_size=2**24,
                        proxy=None,  # the NAS is local; never route it through a proxy from the environment
                    )
                )
                break
            except (OSError, WebSocketException, TimeoutError) as exc:
                if attempt == CONNECT_ATTEMPTS - 1:
                    raise TrueNASError(f"couldn't reach TrueNAS at {self.host}: {exc}") from exc
                time.sleep(CONNECT_RETRY_SECONDS)
        answer = self.call(
            "auth.login_ex",
            {"mechanism": "API_KEY_PLAIN", "username": self.username, "api_key": self.api_key},
        )
        if not isinstance(answer, dict) or answer.get("response_type") != "SUCCESS":
            self.close()
            kind = answer.get("response_type") if isinstance(answer, dict) else answer
            raise TrueNASError(f"TrueNAS didn't accept the API key ({kind})")
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self._ws = None
        self._stack.close()

    def _send(self, method: str, params: list) -> int:
        request_id = next(self._ids)
        try:
            self._ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
        except WebSocketException as exc:
            raise TrueNASError(f"lost the connection to TrueNAS: {exc}") from exc
        return request_id

    def call(self, method: str, *params):
        """One call, waited for. Anything else arriving meanwhile (job
        progress, the answer to a job started earlier) is passed over."""
        request_id = self._send(method, list(params))
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TrueNASError(f"TrueNAS didn't answer {method} in time")
            try:
                message = json.loads(self._ws.recv(timeout=remaining))
            except TimeoutError:
                continue
            except (WebSocketException, ValueError) as exc:
                raise TrueNASError(f"lost the connection to TrueNAS: {exc}") from exc
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] or {}
                reason = (error.get("data") or {}).get("reason") or error.get("message") or "unknown error"
                raise TrueNASError(f"{method}: {reason}")
            return message.get("result")

    def start_job(self, method: str, *params) -> int:
        """Starts a job and returns its id without waiting for it to finish.

        The id is found by asking for the newest job of this method above
        the newest one there was before — rather than from the progress
        notifications, whose shape is TrueNAS's to change."""
        before = self.call("core.get_jobs", [], {"order_by": ["-id"], "limit": 1})
        floor = before[0]["id"] if before else 0
        self._send(method, list(params))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            jobs = self.call(
                "core.get_jobs", [["method", "=", method], ["id", ">", floor]], {"order_by": ["-id"], "limit": 1}
            )
            if jobs:
                return jobs[0]["id"]
            time.sleep(0.5)
        raise TrueNASError(f"TrueNAS didn't start {method}")

    def job(self, job_id: int) -> dict | None:
        jobs = self.call("core.get_jobs", [["id", "=", job_id]])
        return jobs[0] if jobs else None

    def wait_for_job(self, job_id: int, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            job = self.job(job_id)
            if job is None:
                raise TrueNASError(f"TrueNAS lost track of job {job_id}")
            if job.get("state") in FINISHED:
                return job
            if time.monotonic() > deadline:
                raise TrueNASError(f"job {job_id} is still {job.get('state')}")
            time.sleep(2)

    # -- apps --

    def app_config(self, app_name: str) -> dict:
        """A Custom App's compose configuration, as a dict."""
        answer = self.call("app.config", app_name)
        if not isinstance(answer, dict) or not isinstance(answer.get("services"), dict):
            raise TrueNASError(f"{app_name} isn't a Custom App TrueNAS can show the compose file of")
        return answer

    def start_app_update(self, app_name: str, compose: dict) -> int:
        return self.start_job("app.update", app_name, {"custom_compose_config": compose})


def job_error(job: dict) -> str:
    return str(job.get("error") or job.get("exception") or job.get("state") or "unknown error").strip()
