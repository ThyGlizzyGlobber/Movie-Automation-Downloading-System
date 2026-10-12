"""Finding the right release: turning a chosen title into search queries
(resolve.py, tv_resolve.py), then the two passes every search result goes
through — "is this the title?" (the relevance gates) and "which is best?"
(the quality score) — for movies (score.py), single episodes (tv_score.py)
and whole seasons or series (pack_score.py). One normaliser underneath it
all (normalize.py), whole-token and never substring, and the household's
editable preferences (pipeline_settings.py).

Rules only: nothing here downloads anything. The pipeline that searches,
picks and adds a torrent lives in downloads/."""
