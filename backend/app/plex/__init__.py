"""Plex: the household's library and its sign-in.

client.py talks to plex.tv and to a Plex server over HTTP. linking.py is
sign-in — the admin linking their account and servers, and each person's
own PIN login — using the same device-linking flow Plex's own apps use (no
client secret, no server-side OAuth callback). library.py answers what's
on Plex, from every linked server, and refreshes it after an import.
audio.py puts a newly filed title on the right audio track.

Plex tokens never reach the browser — same rule as the TMDB key and the
qBittorrent credentials. They're kept server-side in the settings table
and on each linked server's row.

Import from the module you need, never from this package: tests patch
library.py's functions where they live, so a call routed around them would
skip the patch."""
