"""Getting a request downloaded: the pipeline that searches qBittorrent,
filters and scores what it finds and adds the winner (pipeline.py), and
the background worker that drives every request through its statuses
(worker.py, with each loop's work in its own module beside it). Sits above
everything it orchestrates — matching, the library, Plex, a box's drives —
and below only the API and the CLI."""
