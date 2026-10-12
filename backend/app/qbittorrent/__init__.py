"""The qBittorrent WebUI client (client.py): searching, adding torrents,
categories and free space. Over the network only — no Docker socket, no
other privileged access. Sits low so the library can read a torrent's
files without depending on the worker that drives downloads."""
