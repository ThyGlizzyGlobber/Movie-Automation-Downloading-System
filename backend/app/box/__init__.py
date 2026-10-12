"""What only a box does: separate hard drives, each its own storage
(drives.py), downloading a failed drive's titles again (drive_recovery.py),
the box's own Plex Media Server (plex.py), its VPN (vpn.py) and where it is
on the home network (address.py).

Every feature here is gated by an env var a box sets and nothing else does
(MEDIA_DRIVES_ROOT, GLUETUN_URL, PLEX_LOCAL_URL, SETUP_CODE), so any other
install behaves exactly as if this package weren't here. Anything that
changes the host goes to the NAS updater through updater/channel.py."""
