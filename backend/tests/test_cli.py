"""The CLI builds its clients from the same place the app does: an install
set up through the wizard has its key, qBittorrent and library folders in
the settings table, not the env."""

from pathlib import Path

from app import cli, config
from app.db import RequestStore


def test_cli_clients_use_what_the_setup_wizard_saved(monkeypatch, tmp_path):
    for name in ("TMDB_API_KEY", "_QBIT_HOST_ENV", "_QBIT_PORT_ENV", "_QBIT_USERNAME_ENV", "_QBIT_PASSWORD_ENV", "_MOVIE_ROOT_ENV"):
        monkeypatch.setattr(config, name, None)
    monkeypatch.setattr(config, "MOVIE_LIBRARY_ROOT", Path("/unconfigured"))
    store = RequestStore(":memory:")
    store.update_settings(
        {
            "tmdb_api_key": "wizard-key",
            "qbt_host": "10.0.0.5",
            "qbt_port": 8090,
            "qbt_username": "wizard",
            "qbt_password": "secret",
            "movie_library_root": str(tmp_path / "Movies"),
        }
    )
    monkeypatch.setattr(cli, "RequestStore", lambda _path: store)
    built = {}
    monkeypatch.setattr(cli, "QBTClient", lambda *a: built.setdefault("qbt", a))
    monkeypatch.setattr(cli, "TMDBClient", lambda key: built.setdefault("tmdb", key))

    opened = cli._store()
    cli._qbt(opened)
    cli._tmdb(opened)

    assert built["qbt"] == ("10.0.0.5", 8090, "wizard", "secret")
    assert built["tmdb"] == "wizard-key"
    assert config.MOVIE_LIBRARY_ROOT == tmp_path / "Movies"
