#!/usr/bin/env python3
"""Turns a fresh TrueNAS 25.10 install into an Obsidian box. Run once per box
at the factory, as root in the TrueNAS shell, before it ships.

Standard library only, so it runs on TrueNAS's own Python.

(Being built up in steps: so far, the qBittorrent settings every box is
seeded with, and filling in truenas/obsidian-box.yaml.)
"""

import base64
import hashlib
import secrets

# The setup code on a box's card: typed by a person, so no letters that
# pass for others (no I, L, O, U; no 0 or 1). Twelve of these is about 60
# bits, and setup only answers on the home network, rate limited.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def setup_code() -> str:
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return "-".join(code[i:i + 4] for i in range(0, 12, 4))


# qBittorrent's own password format: PBKDF2-HMAC-SHA512, 100,000 rounds,
# a 16-byte salt, stored as "@ByteArray(salt:key)" in base64.
_PBKDF2_ROUNDS = 100_000


def qbittorrent_password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()})"


def qbittorrent_conf(password: str) -> str:
    """The qBittorrent.conf a box starts with: its WebUI password (made per
    box), downloads into /media/downloads, and bound to the VPN tunnel's
    interface — a second lock behind gluetun's firewall, so qBittorrent
    won't use any other interface even if one appeared."""
    return f"""[LegalNotice]
Accepted=true

[BitTorrent]
Session\\DefaultSavePath=/media/downloads
Session\\TempPathEnabled=false
Session\\Interface=tun0
Session\\InterfaceName=tun0
Session\\AnonymousModeEnabled=true

[Preferences]
General\\Locale=en
WebUI\\Port=8080
WebUI\\Username=admin
WebUI\\Password_PBKDF2="{qbittorrent_password_hash(password)}"
WebUI\\LocalHostAuth=true
WebUI\\HostHeaderValidation=false
WebUI\\CSRFProtection=true
"""


def fill(template: str, values: dict[str, str]) -> str:
    """truenas/obsidian-box.yaml with its <PLACEHOLDERS> filled in. Refuses
    to leave any unfilled."""
    for key, value in values.items():
        template = template.replace(f"<{key}>", value)
    left = sorted({part.split(">", 1)[0] for part in template.split("<")[1:] if ">" in part and part.split(">", 1)[0].isupper()})
    if left:
        raise ValueError(f"unfilled: {', '.join(left)}")
    return template
