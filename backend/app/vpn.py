"""The box's VPN: which providers it can use, checking what a buyer enters,
and reading whether the tunnel is up.

On a box (truenas/obsidian-box.yaml) qBittorrent has no network but
gluetun's, and gluetun only sends traffic through the VPN tunnel — so
until the buyer's VPN details are in, downloads can't reach anything. The
details are entered here, checked, and left as a request in the data folder
(`updater/vpn-request.json`); truenas/obsidian-updater.py, as root on the
NAS, checks them again for itself and writes them into gluetun's settings
through TrueNAS. The secrets then live only in TrueNAS's root-only copy of
the app's config: the request is deleted once applied, and nothing here
keeps or returns them.

GLUETUN_URL set (it is on a box, never elsewhere) is what makes the VPN
part of this install at all.
"""

import base64
import binascii
import ipaddress
import os
import re
import socket

import requests

from app import updates

GLUETUN_URL = os.environ.get("GLUETUN_URL") or None
GLUETUN_API_KEY = os.environ.get("GLUETUN_API_KEY") or ""

# gluetun's provider names, what each is called in the app, and which kinds
# of connection each can make with which details. Kept to providers whose
# details a buyer can get from their account page; truenas/obsidian-
# updater.py holds the same table and checks against it again.
PROVIDERS: dict[str, dict] = {
    "mullvad": {"label": "Mullvad", "wireguard": ["private_key", "addresses"]},
    "protonvpn": {"label": "Proton VPN", "wireguard": ["private_key"], "openvpn": ["user", "password"]},
    "nordvpn": {"label": "NordVPN", "openvpn": ["user", "password"], "wireguard": ["private_key"]},
    "surfshark": {"label": "Surfshark", "wireguard": ["private_key", "addresses"], "openvpn": ["user", "password"]},
    "private internet access": {"label": "Private Internet Access", "openvpn": ["user", "password"]},
    "ivpn": {"label": "IVPN", "wireguard": ["private_key", "addresses"]},
    "windscribe": {"label": "Windscribe", "wireguard": ["private_key", "addresses", "preshared_key"]},
    "airvpn": {"label": "AirVPN", "wireguard": ["private_key", "addresses", "preshared_key"]},
    "expressvpn": {"label": "ExpressVPN", "openvpn": ["user", "password"]},
    "custom": {
        "label": "Another provider (WireGuard file)",
        "wireguard": ["private_key", "addresses", "public_key", "endpoint_ip", "endpoint_port"],
    },
}
OPTIONAL = {"preshared_key", "server_countries"}


class VPNSettingsError(ValueError):
    """What's wrong with what was entered, in words for the person entering it."""


def managed() -> bool:
    return bool(GLUETUN_URL)


def providers() -> list[dict]:
    return [
        {"id": key, "label": spec["label"], "types": {kind: spec[kind] for kind in ("wireguard", "openvpn") if kind in spec}}
        for key, spec in PROVIDERS.items()
    ]


def _key(value: str, label: str) -> str:
    value = value.strip()
    try:
        if len(base64.b64decode(value, validate=True)) == 32:
            return value
    except (binascii.Error, ValueError):
        pass
    raise VPNSettingsError(f"That {label} doesn't look right: it should be 44 characters ending in '='.")


def _addresses(value: str) -> str:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    try:
        if parts:
            return ",".join(str(ipaddress.ip_interface(part)) for part in parts)
    except ValueError:
        pass
    raise VPNSettingsError("The address should look like 10.64.0.2/32 (more than one: separated by commas).")


def _text(value: str, label: str) -> str:
    value = value.strip()
    if not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise VPNSettingsError(f"Enter your {label}.")
    return value


def clean(body: dict) -> dict:
    """The settings to apply, checked and tidied, or VPNSettingsError."""
    provider = body.get("provider")
    spec = PROVIDERS.get(provider)
    if not spec:
        raise VPNSettingsError("Pick your VPN provider.")
    kind = body.get("type") or ("wireguard" if "wireguard" in spec else "openvpn")
    if kind not in ("wireguard", "openvpn") or kind not in spec:
        raise VPNSettingsError(f"{spec['label']} can't be used with {kind} here.")
    cleaned: dict = {"provider": provider, "type": kind}
    for field in spec[kind]:
        value = str(body.get(field) or "")
        if not value.strip():
            if field in OPTIONAL:
                continue
            raise VPNSettingsError(f"Enter your {_LABELS[field]}.")
        if field in ("private_key", "public_key", "preshared_key"):
            cleaned[field] = _key(value, _LABELS[field])
        elif field == "addresses":
            cleaned[field] = _addresses(value)
        elif field == "endpoint_ip":
            try:
                cleaned[field] = str(ipaddress.ip_address(value.strip()))
            except ValueError:
                raise VPNSettingsError("The server address should be an IP address.") from None
        elif field == "endpoint_port":
            port = int(value) if value.strip().isdigit() else 0
            if not 0 < port < 65536:
                raise VPNSettingsError("The server port should be a number up to 65535.")
            cleaned[field] = str(port)
        else:
            cleaned[field] = _text(value, _LABELS[field])
    countries = str(body.get("server_countries") or "").strip()
    if countries:
        if not re.fullmatch(r"[A-Za-z][A-Za-z ,.'-]{0,199}", countries):
            raise VPNSettingsError("Countries should be names separated by commas, like Sweden, Switzerland.")
        cleaned["server_countries"] = ",".join(part.strip() for part in countries.split(",") if part.strip())
    return cleaned


_LABELS = {
    "private_key": "WireGuard private key",
    "public_key": "server's public key",
    "preshared_key": "pre-shared key",
    "addresses": "WireGuard address",
    "endpoint_ip": "server address",
    "endpoint_port": "server port",
    "user": "VPN username",
    "password": "VPN password",
}


def parse_wireguard_file(text: str) -> dict:
    """A provider's WireGuard .conf as the custom provider's fields. The
    server's name is looked up, since gluetun only takes an IP address."""
    sections: dict[str, dict[str, str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1].strip().lower(), {})
        elif "=" in line and current is not None:
            name, value = line.split("=", 1)
            current[name.strip().lower()] = value.strip()
    interface, peer = sections.get("interface", {}), sections.get("peer", {})
    if not interface.get("privatekey") or not peer.get("publickey") or not peer.get("endpoint"):
        raise VPNSettingsError("That doesn't look like a WireGuard file: it needs an [Interface] and a [Peer] section.")
    host, _, port = peer["endpoint"].rpartition(":")
    host = host.strip("[]")
    try:
        ip = str(ipaddress.ip_address(host))
    except ValueError:
        try:
            ip = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
        except OSError:
            raise VPNSettingsError(f"Couldn't look up the server {host!r} from the WireGuard file.") from None
    fields = {
        "provider": "custom",
        "type": "wireguard",
        "private_key": interface["privatekey"],
        "addresses": ",".join(a for a in interface.get("address", "").split(",") if ":" not in a) or interface.get("address", ""),
        "public_key": peer["publickey"],
        "endpoint_ip": ip,
        "endpoint_port": port,
    }
    if peer.get("presharedkey"):
        fields["preshared_key"] = peer["presharedkey"]
    return fields


# ---------------------------------------------------------------------------
# Handing it to the updater, and what's applied
# ---------------------------------------------------------------------------


def request_path():
    return updates.updater_dir() / "vpn-request.json"


def state_path():
    return updates.updater_dir() / "vpn-state.json"


def request(settings: dict) -> None:
    updates.write_json(request_path(), {**settings, "requested_at": updates.now_iso()})
    os.chmod(request_path(), 0o600)


def applied() -> dict:
    """What the updater last applied (provider, type, countries; never a
    secret), and whether a change is still waiting for it."""
    state = updates.read_json(state_path()) or {}
    return {
        "pending": request_path().exists(),
        "state": state.get("state"),
        "provider": state.get("provider"),
        "type": state.get("type"),
        "server_countries": state.get("server_countries"),
        "message": state.get("message"),
        "at": state.get("at"),
    }


def status() -> dict:
    """Whether the tunnel is up, from gluetun itself, and the address and
    country the internet sees qBittorrent at."""
    headers = {"X-API-Key": GLUETUN_API_KEY}
    try:
        vpn = requests.get(f"{GLUETUN_URL}/v1/vpn/status", headers=headers, timeout=4)
        running = vpn.ok and vpn.json().get("status") == "running"
        ip = requests.get(f"{GLUETUN_URL}/v1/publicip/ip", headers=headers, timeout=4) if running else None
        where = ip.json() if ip is not None and ip.ok else {}
    except (requests.RequestException, ValueError):
        return {"reachable": False, "connected": False, "public_ip": None, "country": None}
    connected = running and bool(where.get("public_ip"))
    return {
        "reachable": True,
        "connected": connected,
        "public_ip": where.get("public_ip") if connected else None,
        "country": where.get("country") if connected else None,
    }
