"""A stand-in for TrueNAS's API: JSON-RPC 2.0 over a TLS WebSocket at
/api/current, with just the methods app/truenas.py and app/updater.py call.

What `app.update` does is up to the test (`on_update`): the unit tests
record the compose config they were sent, and the end-to-end check runs a
real `docker compose up` with it, as TrueNAS would.

Run directly, it serves on a port with a fresh self-signed certificate:

    python tests/fake_truenas.py --port 8443 --compose-project obse2e --compose-file stack.yaml
"""

import argparse
import itertools
import json
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path

from websockets.sync.server import serve

API_USER = "obsidian-updater"
API_KEY = "1-test-key"


def self_signed_context(folder: Path) -> ssl.SSLContext:
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=truenas.local",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def _matches(job: dict, filters: list) -> bool:
    for field, op, value in filters:
        actual = job.get(field)
        if op == "=" and actual != value:
            return False
        if op == ">" and not (actual is not None and actual > value):
            return False
    return True


class FakeTrueNAS:
    def __init__(self, app_name: str, compose: dict, on_update=None, job_seconds: float = 0.2):
        self.app_name = app_name
        self.compose = compose
        self.on_update = on_update or (lambda compose: None)
        self.job_seconds = job_seconds
        self.jobs: list[dict] = [{"id": 7, "method": "pool.scrub", "state": "SUCCESS", "error": None}]
        self.updates: list[dict] = []
        self.logins: list[dict] = []
        self._ids = itertools.count(8)
        self._lock = threading.Lock()

    # -- methods --

    def auth_login_ex(self, data):
        self.logins.append(data)
        ok = data.get("mechanism") == "API_KEY_PLAIN" and data.get("username") == API_USER and data.get("api_key") == API_KEY
        return {"response_type": "SUCCESS" if ok else "AUTH_ERR"}

    def core_get_jobs(self, filters=(), options=None):
        options = options or {}
        with self._lock:
            jobs = [dict(job) for job in self.jobs if _matches(job, filters)]
        if "-id" in options.get("order_by", []):
            jobs.sort(key=lambda job: -job["id"])
        if options.get("limit"):
            jobs = jobs[: options["limit"]]
        return jobs

    def app_config(self, name):
        if name != self.app_name:
            raise LookupError(f"{name} not found")
        return json.loads(json.dumps(self.compose))

    def app_update(self, name, values):
        if name != self.app_name:
            raise LookupError(f"{name} not found")
        job = {"id": next(self._ids), "method": "app.update", "state": "RUNNING", "error": None}
        with self._lock:
            self.jobs.append(job)

        def run():
            try:
                compose = values["custom_compose_config"]
                self.updates.append(compose)
                self.on_update(compose)
                self.compose = compose
                state, error = "SUCCESS", None
            except Exception as exc:  # noqa: BLE001 — reported as the job's error, like TrueNAS does
                state, error = "FAILED", str(exc)
            with self._lock:
                job.update(state=state, error=error)

        threading.Timer(self.job_seconds, run).start()
        return job  # the real API answers only when the job ends; the client never waits for this

    # -- transport --

    def handle(self, ws):
        authed = False
        for raw in ws:
            request = json.loads(raw)
            method, params = request["method"], request.get("params", [])
            try:
                if method != "auth.login_ex" and not authed:
                    raise PermissionError("Not authenticated")
                result = getattr(self, method.replace(".", "_"))(*params)
                if method == "auth.login_ex":
                    authed = result["response_type"] == "SUCCESS"
                if method == "app.update":
                    continue  # a job: no answer until it ends, which the client doesn't wait for
                ws.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))
            except Exception as exc:  # noqa: BLE001
                ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": request["id"],
                    "error": {"code": -32001, "message": "Method call error", "data": {"reason": str(exc)}},
                }))

    def serve(self, port: int, context: ssl.SSLContext):
        return serve(self.handle, "0.0.0.0", port, ssl=context)


def main():  # pragma: no cover — the end-to-end check's entry point
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--app-name", default="obsidian")
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--compose-file", required=True)
    args = parser.parse_args()
    compose_file = Path(args.compose_file)

    def compose_up(compose):
        compose_file.write_text(yaml.safe_dump(compose, sort_keys=False))
        subprocess.run(
            ["docker", "compose", "-p", args.compose_project, "-f", str(compose_file), "up", "-d"],
            check=True, capture_output=True, text=True,
        )

    fake = FakeTrueNAS(args.app_name, yaml.safe_load(compose_file.read_text()), on_update=compose_up)
    with tempfile.TemporaryDirectory() as folder:
        with fake.serve(args.port, self_signed_context(Path(folder))) as server:
            print(f"fake TrueNAS on wss://0.0.0.0:{args.port}/api/current", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()
