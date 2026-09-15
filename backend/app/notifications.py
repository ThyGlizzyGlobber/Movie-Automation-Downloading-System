"""Notifications: a per-user inbox of "your request landed / failed"
records, delivered three ways — the bell in the app (polled), the
browser's own Notification API while a tab is open (the frontend's job),
and Web Push to any device that subscribed (this module, via pywebpush
with VAPID keys generated once and kept in the settings table).

Who gets told: the requester, when they asked to be notified (the
request sheet's toggle, defaulting to their own preference), plus anyone
who turned on "also tell me about the household's requests"."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db import RequestRow, RequestStore

logger = logging.getLogger(__name__)

SETTLED_OK = {"complete"}
SETTLED_FAILED = {"failed", "no qualifying results", "insufficient free space", "downloaded, not filed"}
SETTLED = SETTLED_OK | SETTLED_FAILED

try:  # optional at import time so the test suite never needs the library
    from pywebpush import WebPushException, webpush
    from py_vapid import Vapid
except ImportError:  # pragma: no cover
    webpush = None
    WebPushException = Exception
    Vapid = None


def push_available() -> bool:
    return webpush is not None and Vapid is not None


def ensure_vapid_keys(store: "RequestStore") -> dict | None:
    """Returns {"public": <base64url>, "private": <pem>}; generates and
    persists the pair on first use."""
    if not push_available():
        return None
    settings = store.get_settings()
    if settings.get("vapid_public_key") and settings.get("vapid_private_key"):
        return {"public": settings["vapid_public_key"], "private": settings["vapid_private_key"]}
    vapid = Vapid()
    vapid.generate_keys()
    private_pem = vapid.private_pem().decode()
    # Applications need the raw uncompressed public point, base64url.
    import base64

    from cryptography.hazmat.primitives import serialization

    raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    store.update_settings({"vapid_public_key": public, "vapid_private_key": private_pem})
    return {"public": public, "private": private_pem}


def request_label(row: "RequestRow") -> str:
    if row.media_type == "episode" and row.season_number is not None and row.episode_number is not None:
        return f"{row.title} S{row.season_number:02d}E{row.episode_number:02d}"
    if row.media_type == "pack":
        if row.season_number is None:
            return f"{row.title} (whole series)"
        return f"{row.title} (season {row.season_number})"
    return f"{row.title}{f' ({row.release_year})' if row.release_year else ''}"


def message_for(row: "RequestRow") -> tuple[str, str, str]:
    """(kind, title, body) for a settled request."""
    label = request_label(row)
    if row.status in SETTLED_OK:
        return "landed", f"{label} is in Plex", "Ready to watch."
    if row.status == "no qualifying results":
        return "failed", f"Nothing found for {label}", "Meridian will keep looking if the show is followed."
    if row.status == "insufficient free space":
        return "failed", f"Not enough room for {label}", "Free up space and try again."
    if row.status == "downloaded, not filed":
        return "failed", f"{label} downloaded but not filed", "It needs a hand to land in Plex."
    return "failed", f"{label} failed", row.error_message or "Something went wrong."


def recipients_for(store: "RequestStore", row: "RequestRow") -> list[str]:
    users = store.list_users()
    out: list[str] = []
    for u in users:
        if u.plex_user_id == row.requested_by_plex_id:
            if row.notify or (row.notify is None and u.notify_own):
                out.append(u.plex_user_id)
        elif u.notify_household:
            out.append(u.plex_user_id)
    return out


def send_push_to_user(store: "RequestStore", plex_user_id: str, payload: dict) -> int:
    """Pushes `payload` to every device the user subscribed; drops
    subscriptions the push service reports as gone. Returns deliveries."""
    if not push_available():
        return 0
    keys = ensure_vapid_keys(store)
    if not keys:
        return 0
    sent = 0
    for sub in store.list_push_subscriptions(plex_user_id):
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(payload),
                vapid_private_key=keys["private"],
                vapid_claims={"sub": "mailto:meridian@localhost"},
                ttl=3600,
            )
            sent += 1
        except WebPushException as exc:  # pragma: no cover — depends on a live push service
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                store.delete_push_subscription(sub["endpoint"])
            else:
                logger.info("push to %s failed: %s", plex_user_id, exc)
        except Exception as exc:  # noqa: BLE001 — pragma: no cover
            logger.info("push to %s failed: %s", plex_user_id, exc)
    return sent


def notify_settled(store: "RequestStore", row: "RequestRow") -> int:
    """Records (and pushes) a notification for every recipient of a
    request that just settled. Returns how many inbox rows were added."""
    kind, title, body = message_for(row)
    added = 0
    for user_id in recipients_for(store, row):
        store.add_notification(user_id, row.id, kind, title, body)
        added += 1
        send_push_to_user(store, user_id, {"title": title, "body": body, "url": "/#/requests", "kind": kind})
    return added


def sweep(store: "RequestStore") -> int:
    """Called by the worker on every download poll: every settled request
    not yet notified gets its notifications, then is marked so it never
    fires twice. Cancelled requests are skipped on purpose."""
    total = 0
    for row in store.list_unnotified_settled(sorted(SETTLED)):
        try:
            total += notify_settled(store, row)
        finally:
            store.mark_notified(row.id)
    return total
