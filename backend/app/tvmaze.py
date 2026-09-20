"""Episode release *times*, which TMDB doesn't carry.

TMDB gives an episode a date and nothing more, so the air buffer that
delays a search after release could only ever be measured from midnight
UTC on that date. For a show broadcast in US prime time that anchor is
roughly a day early: Lanterns S01E06 is dated 2026-09-20 on TMDB and
actually landed at 2026-09-21T01:00Z (21:00 America/New_York), so a
15-hour buffer expired ten hours *before* the episode existed and the
next show check grabbed whatever had been uploaded into that gap — the
exact failure the buffer was added to prevent.

TVmaze publishes `airstamp`, a full UTC timestamp per episode, needs no
API key or account, and can be reached from a TMDB show through its
TheTVDB or IMDb id. It is used for that one field only: TMDB remains the
metadata source for everything else.

Best effort throughout. TVmaze's coverage is good but not universal, and
this is a network call in the middle of a scheduled check — every
failure path returns "no timestamps" so the caller falls back to the
date-based rule rather than a followed show quietly never updating
again.
"""

import logging
from datetime import datetime

import requests

from app.cache import ttl_cache

BASE_URL = "https://api.tvmaze.com"
TIMEOUT_SECONDS = 10
# Episode listings barely change — a schedule shifts days ahead of time,
# not minutes — and a followed show is checked every few hours, so this
# only has to stop one lookup per show per check turning into two calls
# every time. Well inside TVmaze's ~20-requests-per-10-seconds limit at
# household scale.
LOOKUP_TTL_SECONDS = 6 * 3600

logger = logging.getLogger(__name__)


def _parse_airstamp(value: str | None) -> datetime | None:
    """TVmaze sends RFC 3339 with a real offset ("+00:00", "-04:00").
    Anything unparseable is treated as absent, not as an error: a single
    malformed episode shouldn't cost the whole season its timestamps."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class TVMazeClient:
    """Read-only, unauthenticated. Constructed once at startup like the
    other clients, so a test can hand in its own session."""

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict | None = None):
        response = self.session.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT_SECONDS)
        if response.status_code == 404:
            return None  # TVmaze simply doesn't have this show
        if not response.ok:
            raise requests.HTTPError(f"TVmaze {path} failed: {response.status_code}")
        return response.json()

    @ttl_cache(LOOKUP_TTL_SECONDS)
    def show_id(self, tvdb_id: int | None = None, imdb_id: str | None = None) -> int | None:
        """TVmaze's own id for a show identified by its TheTVDB or IMDb
        id — the two external ids TMDB hands back. TheTVDB first because
        it is the one TVmaze indexes most completely for television."""
        for param, value in (("thetvdb", tvdb_id), ("imdb", imdb_id)):
            if not value:
                continue
            data = self._get("/lookup/shows", {param: value})
            if data and data.get("id"):
                return int(data["id"])
        return None

    @ttl_cache(LOOKUP_TTL_SECONDS)
    def airstamps(self, tvmaze_show_id: int) -> dict[tuple[int, int], datetime]:
        """`{(season, episode): released_at_utc}` for a whole show.

        Whole show rather than per season: TVmaze serves the full episode
        list in one call, and a show check looks at more than one season
        on a backfill."""
        data = self._get(f"/shows/{tvmaze_show_id}/episodes")
        out: dict[tuple[int, int], datetime] = {}
        for episode in data or []:
            season, number = episode.get("season"), episode.get("number")
            stamp = _parse_airstamp(episode.get("airstamp"))
            if season is not None and number is not None and stamp is not None:
                out[(int(season), int(number))] = stamp
        return out

    def airstamps_for_show(self, tvdb_id: int | None, imdb_id: str | None) -> dict[tuple[int, int], datetime]:
        """The whole lookup in one call, and the only method callers want.
        Returns `{}` for anything that doesn't work out — no match, no
        external ids, TVmaze unreachable — which is the signal to fall
        back to TMDB's date."""
        if not tvdb_id and not imdb_id:
            return {}
        try:
            show_id = self.show_id(tvdb_id=tvdb_id, imdb_id=imdb_id)
            if show_id is None:
                return {}
            return self.airstamps(show_id)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.info("tvmaze lookup failed for tvdb=%s imdb=%s: %s", tvdb_id, imdb_id, exc)
            return {}


def season_airstamps(
    airstamps: dict[tuple[int, int], datetime], season_number: int
) -> dict[int, datetime]:
    """The `{episode_number: released_at}` slice one season's check needs,
    out of a whole show's map."""
    return {episode: stamp for (season, episode), stamp in airstamps.items() if season == season_number}
