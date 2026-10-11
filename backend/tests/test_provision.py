"""truenas/provision.py, the factory script: the qBittorrent settings each box
starts with, and filling in truenas/obsidian-box.yaml."""

import base64
import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

TRUENAS = Path(__file__).resolve().parents[2] / "truenas"
spec = importlib.util.spec_from_file_location("provision", TRUENAS / "provision.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def test_the_password_hash_is_qbittorrent_s_own_format():
    salt = bytes(range(16))
    stored = provision.qbittorrent_password_hash("correct horse", salt)
    match = re.fullmatch(r"@ByteArray\(([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)\)", stored)
    assert match
    assert base64.b64decode(match[1]) == salt
    expected = hashlib.pbkdf2_hmac("sha512", b"correct horse", salt, 100_000)
    assert base64.b64decode(match[2]) == expected


def test_every_box_gets_its_own_salt():
    assert provision.qbittorrent_password_hash("same") != provision.qbittorrent_password_hash("same")


def test_qbittorrent_is_bound_to_the_tunnel_and_saves_into_the_shared_media():
    conf = provision.qbittorrent_conf("pw")
    assert "Session\\Interface=tun0" in conf and "Session\\InterfaceName=tun0" in conf
    assert "Session\\DefaultSavePath=/media/downloads" in conf
    assert "WebUI\\Username=admin" in conf
    assert '"pw"' not in conf and "=pw\n" not in conf  # only the hash is stored


def test_filling_the_box_leaves_nothing_unfilled():
    template = (TRUENAS / "obsidian-box.yaml").read_text()
    placeholders = sorted(set(re.findall(r"<([A-Z_]+)>", template)))
    filled = provision.fill(template, {name: f"value-{name}" for name in placeholders})
    assert "<" not in re.sub(r"#.*", "", filled)
    with pytest.raises(ValueError, match="unfilled: TZ"):
        provision.fill(template, {name: "x" for name in placeholders if name != "TZ"})


def test_qbittorrent_has_no_network_but_the_vpn_s():
    template = (TRUENAS / "obsidian-box.yaml").read_text()
    qbittorrent = template[template.index("  qbittorrent:"):template.index("  plex:")]
    assert 'network_mode: "service:gluetun"' in qbittorrent
    assert "ports:" not in qbittorrent


def test_a_setup_code_is_easy_to_read_off_a_card():
    codes = {provision.setup_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
        assert not set(code) & set("ILOU01")
