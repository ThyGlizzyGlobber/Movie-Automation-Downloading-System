"""The library on disk: filing a finished download where Plex reliably
recognises it, and cleaning up after.

filing.py picks the video out of a torrent and hardlinks it into Plex's
layout, named from this app's own resolved identity rather than the
release's filename. packs.py does the same for a whole season or series.
audio.py makes the household's language the default track, and
containers.py probes files and rewraps what Plex can't play directly.
reconcile.py finds source torrents whose cleanup was lost."""
