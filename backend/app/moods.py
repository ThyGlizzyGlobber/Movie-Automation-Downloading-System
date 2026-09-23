"""Named rows — the "Pitch-Black Comedies" half of a streaming home page.

A row called "Comedy" is a filing cabinet. A row called "Comedies That Go
Somewhere Dark" is an offer: it tells you what the evening would feel like,
and you know within a word whether you want it. That difference is most of
what makes the big services' home pages feel curated rather than generated,
and none of it comes from better recommendation maths — it comes from
someone writing the name and choosing what belongs under it.

So the catalogue below is hand-written. Each mood pairs a name worth reading
with a TMDB query narrow enough to earn it: "Quietly Devastating" is not
simply drama, it is drama with a rating floor and a vote floor underneath so
the floor means something. A mood whose query is just its genre id would be
the filing cabinet again, with a better label on the drawer.

Which moods appear is steered by what this person actually watches, and
rotated daily by the same clock as everything else on the page — see
taste.py. The genre tags on each mood are how those two meet: they are not
the query, they are what the query is *about*, which is the question "would
this person care".
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

# TMDB genre ids. Movies and TV disagree — TV has no "Action" but an
# "Action & Adventure" (10759), and its sci-fi and fantasy share one id
# (10765) where movies split them (878/14). Hence the media_type on every
# mood: the same English word is a different number depending on which
# half of TMDB you are asking.
ACTION, ADVENTURE, ANIMATION, COMEDY, CRIME = 28, 12, 16, 35, 80
DOCUMENTARY, DRAMA, FAMILY, FANTASY, HISTORY = 99, 18, 10751, 14, 36
HORROR, MUSIC, MYSTERY, ROMANCE, SCIFI = 27, 10402, 9648, 10749, 878
THRILLER, WAR, WESTERN = 53, 10752, 37
TV_ACTION, TV_SCIFI_FANTASY, TV_WAR_POLITICS = 10759, 10765, 10768

# How many named rows a page carries, and the pool they're drawn from. Same
# shape as the seed draw in taste.py, for the same reason: a page that shows
# the same three named rows forever is a page with three more fixed rows.
MOODS_PER_DAY = 3
# Widened with the catalogue: a pool of 8 against 45 moods would hand a
# drama watcher the same eight candidates every day and call it variety.
MOOD_POOL = 14

# Under this many votes a TMDB rating is one person's opinion, not a rating.
# Every mood that filters on quality carries a floor, because "highly rated"
# without one reliably surfaces an unreleased film with a single 10.
_CREDIBLE = 300
_WELL_KNOWN = 1500


@dataclass(frozen=True)
class Mood:
    """One named row: what it's called, what's in it, what it's about."""

    key: str
    title: str
    media_type: str
    # What this row is *about*, for matching against someone's taste. Kept
    # apart from the query on purpose — "Nineties Action" is about action
    # whether or not the query happens to pin a decade.
    genres: tuple[int, ...]
    params: dict = field(default_factory=dict)


CATALOGUE: list[Mood] = [
    # Names run from plainly descriptive ("Crime Comedies") to evocative
    # ("Best With the Lights Off") on purpose. A page of nothing but clever
    # names is exhausting and starts to feel like it is hiding what's in the
    # row; a page of nothing but genre pairs is the filing cabinet. The mix
    # is what reads as curated.

    # -- Movies: genre pairs, said plainly ------------------------------
    Mood("mood:crime-comedy", "Crime Comedies", "movie", (COMEDY, CRIME),
         {"with_genres": f"{COMEDY},{CRIME}", "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),
    Mood("mood:action-scifi", "Action Sci-Fi", "movie", (ACTION, SCIFI),
         {"with_genres": f"{ACTION},{SCIFI}", "vote_count.gte": _CREDIBLE, "sort_by": "popularity.desc"}),
    Mood("mood:action-blockbusters", "Blockbuster Action", "movie", (ACTION, ADVENTURE),
         {"with_genres": ACTION, "vote_count.gte": 4000, "sort_by": "popularity.desc"}),
    Mood("mood:comedy-blockbusters", "Comedy Blockbusters", "movie", (COMEDY,),
         {"with_genres": COMEDY, "vote_count.gte": 3000, "sort_by": "popularity.desc"}),
    Mood("mood:romantic-comedy", "Romantic Comedies", "movie", (COMEDY, ROMANCE),
         {"with_genres": f"{COMEDY},{ROMANCE}", "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),
    Mood("mood:crime-thriller", "Crime Thrillers", "movie", (CRIME, THRILLER),
         {"with_genres": f"{CRIME},{THRILLER}", "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),
    Mood("mood:fantasy-adventure", "Fantasy Adventures", "movie", (FANTASY, ADVENTURE),
         {"with_genres": f"{FANTASY},{ADVENTURE}", "vote_count.gte": _CREDIBLE, "sort_by": "popularity.desc"}),
    Mood("mood:historical-drama", "Historical Dramas", "movie", (HISTORY, DRAMA),
         {"with_genres": f"{HISTORY},{DRAMA}", "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),
    Mood("mood:animated-family", "Animated Family Films", "movie", (ANIMATION, FAMILY),
         {"with_genres": f"{ANIMATION},{FAMILY}", "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),
    Mood("mood:musicals", "Turn the Volume Up", "movie", (MUSIC,),
         {"with_genres": MUSIC, "vote_count.gte": 150, "sort_by": "vote_average.desc"}),
    Mood("mood:war-epics", "War Epics", "movie", (WAR,),
         {"with_genres": WAR, "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),
    Mood("mood:mystery-thriller", "Mystery Thrillers", "movie", (MYSTERY, THRILLER),
         {"with_genres": f"{MYSTERY},{THRILLER}", "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),

    # -- Movies: the ones worth a better name ---------------------------
    Mood("mood:dark-comedy", "Comedies That Go Somewhere Dark", "movie", (COMEDY, CRIME),
         {"with_genres": f"{COMEDY},{CRIME}", "vote_average.gte": 7, "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    Mood("mood:quietly-devastating", "Quietly Devastating", "movie", (DRAMA,),
         {"with_genres": DRAMA, "vote_average.gte": 7.5, "vote_count.gte": _WELL_KNOWN,
          "sort_by": "vote_average.desc"}),
    Mood("mood:nineties-action", "Nineties Action, No Notes", "movie", (ACTION, ADVENTURE),
         {"with_genres": ACTION, "primary_release_date.gte": "1990-01-01",
          "primary_release_date.lte": "1999-12-31", "vote_count.gte": _CREDIBLE, "sort_by": "popularity.desc"}),
    Mood("mood:eighties", "Straight Out of the Eighties", "movie", (ACTION, COMEDY),
         {"primary_release_date.gte": "1980-01-01", "primary_release_date.lte": "1989-12-31",
          "vote_count.gte": _WELL_KNOWN, "sort_by": "popularity.desc"}),
    Mood("mood:thinking-scifi", "Sci-Fi That Earns Its Ending", "movie", (SCIFI,),
         {"with_genres": SCIFI, "vote_average.gte": 7, "vote_count.gte": _WELL_KNOWN,
          "sort_by": "vote_average.desc"}),
    Mood("mood:imaginative-comedy", "Comedies With an Odd Idea", "movie", (COMEDY, FANTASY),
         {"with_genres": f"{COMEDY},{FANTASY}", "vote_count.gte": _CREDIBLE, "sort_by": "vote_average.desc"}),
    Mood("mood:old-horror", "Frightening Long Before You Were Born", "movie", (HORROR,),
         {"with_genres": HORROR, "primary_release_date.lte": "1999-12-31", "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    Mood("mood:lights-off-horror", "Best With the Lights Off", "movie", (HORROR, THRILLER),
         {"with_genres": f"{HORROR},{THRILLER}", "vote_count.gte": _CREDIBLE, "sort_by": "popularity.desc"}),
    Mood("mood:grown-up-animation", "Animated, and Not for the Kids", "movie", (ANIMATION,),
         {"with_genres": ANIMATION, "without_genres": FAMILY, "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    Mood("mood:long-crime", "Crime Sagas Worth the Runtime", "movie", (CRIME, DRAMA),
         {"with_genres": f"{CRIME},{DRAMA}", "with_runtime.gte": 135, "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    Mood("mood:delivered", "Blockbusters That Actually Delivered", "movie", (ACTION, ADVENTURE),
         {"with_genres": f"{ACTION},{ADVENTURE}", "vote_average.gte": 7, "vote_count.gte": 4000,
          "sort_by": "vote_average.desc"}),
    Mood("mood:unsugared-romance", "Romance Without the Sugar", "movie", (ROMANCE, DRAMA),
         {"with_genres": f"{ROMANCE},{DRAMA}", "vote_average.gte": 7, "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    Mood("mood:true-and-strange", "True, and Hard to Believe", "movie", (DOCUMENTARY, HISTORY),
         {"with_genres": DOCUMENTARY, "vote_count.gte": 150, "sort_by": "vote_average.desc"}),
    Mood("mood:sunday-westerns", "Sunday Afternoon Westerns", "movie", (WESTERN,),
         {"with_genres": WESTERN, "vote_count.gte": 150, "sort_by": "vote_average.desc"}),
    Mood("mood:cold-war", "War Films That Don't Salute", "movie", (WAR, DRAMA),
         {"with_genres": f"{WAR},{DRAMA}", "vote_average.gte": 7, "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    Mood("mood:puzzle-box", "You'll Want to Discuss These After", "movie", (MYSTERY, THRILLER),
         {"with_genres": f"{MYSTERY},{THRILLER}", "vote_average.gte": 7, "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    Mood("mood:family-night", "Nobody Will Complain", "movie", (FAMILY, ADVENTURE),
         {"with_genres": FAMILY, "vote_average.gte": 6.8, "vote_count.gte": _CREDIBLE,
          "sort_by": "vote_average.desc"}),
    # Practical rows. "How long is it" is a real question on a weeknight,
    # and no genre answers it.
    Mood("mood:short", "Done in Under 100 Minutes", "movie", (COMEDY, THRILLER),
         {"with_runtime.lte": 100, "vote_average.gte": 7, "vote_count.gte": _WELL_KNOWN,
          "sort_by": "vote_average.desc"}),
    Mood("mood:settle-in", "Settle In, It's a Long One", "movie", (DRAMA, HISTORY),
         {"with_runtime.gte": 150, "vote_average.gte": 7.5, "vote_count.gte": _WELL_KNOWN,
          "sort_by": "vote_average.desc"}),
    Mood("mood:overlooked", "Adored, and Barely Seen", "movie", (DRAMA,),
         {"vote_average.gte": 7.5, "vote_count.gte": 200, "sort_by": "vote_average.desc"}),
    Mood("mood:subtitles", "Worth Reading the Subtitles", "movie", (DRAMA, THRILLER),
         {"without_genres": FAMILY, "with_original_language": "ko", "vote_count.gte": 200,
          "sort_by": "vote_average.desc"}),
    Mood("mood:out-this-decade", "The Best of the Decade So Far", "movie", (DRAMA, ACTION),
         {"primary_release_date.gte": "2020-01-01", "vote_average.gte": 7.5, "vote_count.gte": _WELL_KNOWN,
          "sort_by": "vote_average.desc"}),

    # -- TV ---------------------------------------------------------------
    Mood("mood:tv-crime", "One More Episode, Then Bed", "tv", (CRIME,),
         {"with_genres": CRIME, "vote_count.gte": 200, "sort_by": "vote_average.desc"}),
    Mood("mood:tv-comfort", "Comfort Watching", "tv", (COMEDY,),
         {"with_genres": COMEDY, "vote_average.gte": 7, "vote_count.gte": 200, "sort_by": "vote_average.desc"}),
    Mood("mood:tv-prestige", "Television That Thinks It's Cinema", "tv", (DRAMA,),
         {"with_genres": DRAMA, "vote_average.gte": 8, "vote_count.gte": 400, "sort_by": "vote_average.desc"}),
    Mood("mood:tv-built-worlds", "Worlds Built From Scratch", "tv", (TV_SCIFI_FANTASY,),
         {"with_genres": TV_SCIFI_FANTASY, "vote_count.gte": 200, "sort_by": "vote_average.desc"}),
    Mood("mood:tv-rabbit-hole", "Documentary Rabbit Holes", "tv", (DOCUMENTARY,),
         {"with_genres": DOCUMENTARY, "vote_count.gte": 80, "sort_by": "vote_average.desc"}),
    Mood("mood:tv-grew-up-with", "Shows That Grew Up With You", "tv", (COMEDY, DRAMA),
         {"with_genres": COMEDY, "first_air_date.gte": "1994-01-01", "first_air_date.lte": "2009-12-31",
          "vote_count.gte": 200, "sort_by": "popularity.desc"}),
    # TMDB's with_type=2 is "miniseries" — the one structural fact about a
    # show that changes whether you start it on a Sunday night.
    Mood("mood:tv-miniseries", "Finished in a Weekend", "tv", (DRAMA, CRIME),
         {"with_type": 2, "vote_average.gte": 7.5, "vote_count.gte": 150, "sort_by": "vote_average.desc"}),
    Mood("mood:tv-crime-comedy", "Crime Comedies", "tv", (CRIME, COMEDY),
         {"with_genres": f"{CRIME},{COMEDY}", "vote_count.gte": 150, "sort_by": "vote_average.desc"}),
    Mood("mood:tv-animation", "Animated Series for Grown-Ups", "tv", (ANIMATION,),
         {"with_genres": ANIMATION, "vote_average.gte": 7.5, "vote_count.gte": 200,
          "sort_by": "vote_average.desc"}),
    Mood("mood:tv-action", "Action & Adventure Series", "tv", (TV_ACTION,),
         {"with_genres": TV_ACTION, "vote_count.gte": 200, "sort_by": "popularity.desc"}),
    Mood("mood:tv-politics", "Power, and the People Who Want It", "tv", (TV_WAR_POLITICS, DRAMA),
         {"with_genres": TV_WAR_POLITICS, "vote_count.gte": 100, "sort_by": "vote_average.desc"}),
]


def affinity_from_rows(rows) -> Counter:
    """What this person's recommendations are made of, by genre.

    Read off the rows already built rather than fetched: every TMDB result
    carries `genre_ids`, and those rows are by construction "things like
    what they watch". So the affinity costs nothing, and it describes their
    taste at one remove — which is the right remove, because a row about
    *them* should point somewhere adjacent to what they already have, not
    back at it.
    """
    counts: Counter = Counter()
    for row in rows:
        for item in getattr(row, "items", []):
            for genre_id in item.get("genre_ids") or []:
                counts[int(genre_id)] += 1
    return counts


def score(mood: Mood, affinity: Counter) -> int:
    """How much this person is likely to care about a named row."""
    return sum(affinity.get(genre_id, 0) for genre_id in mood.genres)


def pick_moods(
    affinity: Counter,
    rng: random.Random,
    count: int = MOODS_PER_DAY,
    pool: int = MOOD_POOL,
    catalogue: list[Mood] | None = None,
) -> list[Mood]:
    """Today's named rows: sampled from the ones that suit this person.

    Ranked by affinity, then sampled from the top `pool` — the same shape
    as the seed draw, and for the same reason. Taking the top three would
    give someone who watches horror the same three horror rows forever.

    With no affinity at all — a brand new household, nothing requested yet
    — every mood scores zero and the pool is the whole catalogue. That is
    the right failure: a new user gets a varied, well-named page that
    rotates, rather than an empty one waiting for them to earn it.
    """
    moods = list(catalogue if catalogue is not None else CATALOGUE)
    if any(score(mood, affinity) for mood in moods):
        moods.sort(key=lambda mood: (-score(mood, affinity), mood.key))
        eligible = moods[:pool]
    else:
        eligible = moods
    if len(eligible) <= count:
        return eligible
    return rng.sample(eligible, count)
