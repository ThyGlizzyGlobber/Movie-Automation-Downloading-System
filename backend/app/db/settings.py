"""RequestStore's settings JSON and the first-run setup token."""

import json
import os
import secrets


class StoreSettings:
    """Mixed into RequestStore (store.py). Relies on its `_conn` and `_lock`."""

    # -- settings --

    def get_settings(self) -> dict:
        row = self._conn.execute("SELECT data_json FROM settings WHERE id = 1").fetchone()
        return json.loads(row["data_json"]) if row else {}

    def update_settings(self, patch: dict) -> dict:
        """Shallow-merges `patch` into the single settings row. A key set
        to `None` (e.g. unlinking Plex) is stored as null, not removed —
        callers read it back with the same `.get(...)` either way."""
        with self._lock:
            merged = {**self.get_settings(), **patch}
            self._conn.execute("UPDATE settings SET data_json = ? WHERE id = 1", (json.dumps(merged),))
            self._conn.commit()
            return merged

    # -- setup bootstrap (frontend migration Part G1) --

    def get_or_create_setup_token(self) -> str:
        """One-time bootstrap secret required by /api/setup/* (and the
        first-run POST /api/plex/link) until initial setup completes —
        closes the race where, on an internet-reachable instance, a
        stranger who reaches an unset-up install first could claim the
        admin slot before the real admin does. Persisted in the settings
        table rather than regenerated per-process, so it survives a
        `--reload` restart during development and stays valid for as long
        as setup remains incomplete, however long that takes.

        A box ships with its own code, printed on the card in its box
        (truenas/provision.py makes it and passes it as SETUP_CODE); that's
        the code from the first boot on. Anywhere else it's made here."""
        settings = self.get_settings()
        token = settings.get("setup_token")
        if not token:
            token = os.environ.get("SETUP_CODE") or secrets.token_urlsafe(32)
            self.update_settings({"setup_token": token})
        return token
