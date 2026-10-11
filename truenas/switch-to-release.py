#!/usr/bin/env python3
"""Moves a git-pull install of Obsidian (truenas/custom-app-compose.yaml:
images built on the NAS from a clone, updated with `git pull`) onto the
published release images, in place, keeping everything else it has.

    python3 switch-to-release.py --app movie-downloader --channel beta
    python3 switch-to-release.py --app movie-downloader --revert <saved file>

It reads the app's config from TrueNAS and changes only this:

  frontend, backend   `build:` becomes the channel's published image
  mounts removed      the clone's source and built site (they'd hide what the
                      image ships), the clone itself, the deploy key
  settings removed    DEPLOY_REPO_PATH, GIT_SSH_KEY_PATH and the deploy timeouts

Every other setting, mount, port and service (a Cloudflare tunnel, say) is
kept exactly as it is, so the data folder, keys and library paths carry
over. It shows the changes — setting names, never their values — and asks
before applying. The current config is saved first to a root-only file
beside this script; `--revert` with that file puts it back. If the switched
app doesn't come up, it puts it back by itself.

Then set up the updater (README › Releases › Setting up updates).
Standard library only, run as root in the TrueNAS shell.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

IMAGE_PREFIX = "ghcr.io/thyglizzyglobber/"
REGISTRY = "https://ghcr.io"
DROPPED_MOUNTS = {
    "/app/app": "the clone's backend source",
    "/repo": "the git clone",
    "/run/secrets/deploy_key": "the GitHub deploy key",
    "/usr/share/nginx/html": "the clone's built site",
}
DROPPED_SETTINGS = (
    "DEPLOY_REPO_PATH", "GIT_SSH_KEY_PATH", "DEPLOY_TIMEOUT_SECONDS", "DEPLOY_BUILD_TIMEOUT_SECONDS",
)
WAIT_SECONDS = 300


def _target(volume) -> str | None:
    if isinstance(volume, str):
        parts = volume.split(":")
        return parts[1] if len(parts) >= 2 else None
    return (volume or {}).get("target") if isinstance(volume, dict) else None


def _drop_settings(environment):
    """(kept, dropped names), for either form compose allows."""
    if isinstance(environment, dict):
        kept = {k: v for k, v in environment.items() if k not in DROPPED_SETTINGS}
        return kept, [k for k in environment if k in DROPPED_SETTINGS]
    if isinstance(environment, list):
        kept = [e for e in environment if str(e).split("=", 1)[0] not in DROPPED_SETTINGS]
        return kept, [str(e).split("=", 1)[0] for e in environment if e not in kept]
    return environment, []


def convert(compose: dict, channel: str, prefix: str = IMAGE_PREFIX) -> tuple[dict, list[str]]:
    """The release version of a git-pull install's config, and what changed."""
    switched = json.loads(json.dumps(compose))
    services = switched.get("services") or {}
    changes: list[str] = []
    for name in ("frontend", "backend"):
        service = services.get(name)
        if not isinstance(service, dict):
            raise SystemExit(f"The app has no {name!r} service, so it isn't an Obsidian install this can switch.")
        if "build" not in service:
            image = service.get("image", "")
            if image.startswith(f"{prefix}obsidian-{name}:"):
                raise SystemExit(f"The app already runs release images ({image}). Nothing to switch.")
            raise SystemExit(f"The {name} service neither builds Obsidian nor runs its release image.")
        service.pop("build")
        service["image"] = f"{prefix}obsidian-{name}:{channel}"
        changes.append(f"{name}: built on the NAS -> {service['image']}")
        kept = []
        for volume in service.get("volumes") or []:
            reason = DROPPED_MOUNTS.get(_target(volume) or "")
            if reason:
                changes.append(f"{name}: no longer mounts {_target(volume)} ({reason})")
            else:
                kept.append(volume)
        if "volumes" in service:
            service["volumes"] = kept
        environment, dropped = _drop_settings(service.get("environment"))
        if dropped:
            service["environment"] = environment
            changes.append(f"{name}: drops {', '.join(dropped)}")
    others = sorted(set(services) - {"frontend", "backend"})
    if others:
        changes.append(f"unchanged: {', '.join(others)}")
    return switched, changes


def describe(compose: dict) -> list[str]:
    """What a config holds, with no values: safe to paste anywhere."""
    lines = []
    for name, service in (compose.get("services") or {}).items():
        service = service or {}
        source = f"image {service['image']}" if "image" in service else "built on the NAS"
        environment = service.get("environment") or {}
        names = sorted(environment) if isinstance(environment, dict) else sorted(str(e).split("=", 1)[0] for e in environment)
        mounts = sorted(_target(v) or "?" for v in service.get("volumes") or [])
        lines.append(f"  {name}: {source}")
        if names:
            lines.append(f"    settings: {', '.join(names)}")
        if mounts:
            lines.append(f"    mounts:   {', '.join(mounts)}")
    return lines


def site_url(compose: dict) -> str:
    for port in ((compose.get("services") or {}).get("frontend") or {}).get("ports") or []:
        if isinstance(port, dict):
            if str(port.get("target")) == "80" and port.get("published"):
                return f"http://127.0.0.1:{port['published']}"
        else:
            parts = str(port).split("/", 1)[0].split(":")
            if len(parts) >= 2 and parts[-1] == "80":
                return f"http://127.0.0.1:{parts[-2]}"
    raise SystemExit("Couldn't find the frontend's published port in the app.")


def _get(url: str, headers: dict | None = None, timeout: float = 10) -> tuple[int, bytes]:
    try:
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(1024 * 1024)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (OSError, ValueError):
        return 0, b""


def published(image: str, channel: str, prefix: str = IMAGE_PREFIX) -> bool:
    repo = f"{prefix.split('/', 1)[1]}{image}".rstrip("/")
    status, body = _get(f"{REGISTRY}/token?scope=repository:{repo}:pull")
    try:
        token = json.loads(body)["token"]
    except (ValueError, KeyError):
        return False
    status, _ = _get(
        f"{REGISTRY}/v2/{repo}/manifests/{channel}",
        {"Authorization": f"Bearer {token}",
         "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json"},
    )
    return status == 200


def running_version(base: str) -> str | None:
    status, body = _get(f"{base}/api/health", timeout=5)
    if status != 200:
        return None
    try:
        return json.loads(body).get("version") or "unknown"
    except (ValueError, AttributeError):
        return None


def wait_until_up(base: str, seconds: int = WAIT_SECONDS, sleep=time.sleep) -> str | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = running_version(base)
        if found and _get(f"{base}/", timeout=5)[0] == 200:
            return found
        sleep(3)
    return None


class Middleware:
    def _client(self):
        try:
            from truenas_api_client import Client
        except ImportError:
            from middlewared.client import Client
        return Client()

    def app_config(self, app: str) -> dict:
        with self._client() as c:
            return c.call("app.config", app)

    def app_update(self, app: str, compose: dict) -> None:
        with self._client() as c:
            c.call("app.update", app, {"custom_compose_config": compose}, job=True)


def save(compose: dict, folder: Path) -> Path:
    path = folder / f"before-switch-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(compose, handle, indent=2)
    return path


def main(argv: list[str] | None = None, middleware=None, ask=input) -> int:
    parser = argparse.ArgumentParser(description="Moves a git-pull Obsidian install onto the release images.")
    parser.add_argument("--app", required=True, help="the app's name in TrueNAS (Apps › Installed)")
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--revert", metavar="FILE", help="put back a config this script saved")
    args = parser.parse_args(argv)
    mw = middleware or Middleware()
    if os.geteuid() != 0:
        raise SystemExit("Run this as root, in the TrueNAS shell.")

    if args.revert:
        original = json.loads(Path(args.revert).read_text())
        print(f"Putting {args.app} back as it was saved in {args.revert}:")
        print("\n".join(describe(original)))
        if ask("Type 'revert' to go ahead: ").strip() != "revert":
            print("Nothing changed.")
            return 1
        mw.app_update(args.app, original)
        found = wait_until_up(site_url(original))
        print(f"Back as it was, running {found}." if found else "Put back, but the site isn't answering yet; check Apps.")
        return 0 if found else 1

    current = mw.app_config(args.app)
    switched, changes = convert(current, args.channel)
    base = site_url(current)
    missing = [name for name in ("obsidian-backend", "obsidian-frontend") if not published(name, args.channel)]
    if missing:
        raise SystemExit(f"{', '.join(missing)} isn't published on the {args.channel} channel yet, so nothing changed.")

    print(f"{args.app} now (running {running_version(base) or 'nothing answering'}):")
    print("\n".join(describe(current)))
    print("\nWhat changes:")
    print("\n".join(f"  - {change}" for change in changes))
    print(f"\nAfterwards:")
    print("\n".join(describe(switched)))
    print("\nYour data folder, keys, library paths, ports and other services stay as they are.")
    if ask("\nType 'switch' to go ahead: ").strip() != "switch":
        print("Nothing changed.")
        return 1

    saved = save(current, Path(__file__).resolve().parent)
    print(f"Saved the current config to {saved} (root only).")
    print("Switching. TrueNAS pulls the images and restarts the app; this takes a minute or two...")
    try:
        mw.app_update(args.app, switched)
        found = wait_until_up(base)
    except Exception as exc:  # noqa: BLE001 — whatever TrueNAS raised, it's the reason
        print(f"TrueNAS couldn't apply it: {getattr(exc, 'error', None) or exc}")
        found = None
    if found:
        print(f"Done: {args.app} runs the {args.channel} release images, version {found}.")
        print("Next: set up the updater (README › Releases › Setting up updates).")
        return 0
    print("It didn't come up, so putting the saved config back...")
    mw.app_update(args.app, current)
    back = wait_until_up(base)
    print(f"Back as it was, running {back}." if back else f"Put back, but not answering yet. The saved config is {saved}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
