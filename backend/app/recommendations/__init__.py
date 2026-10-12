"""The landing pages' rows: which titles go where (feed.py), "Because you
watched…" (taste.py) and the named rows (moods.py).

Pure logic, all of it: every input arrives as an argument, so the rules can
be iterated on without TMDB, Plex or a browser. tests/test_architecture.py
fails if anything here imports from the rest of the app; the plumbing that
feeds it lives in api/recommendations.py."""
