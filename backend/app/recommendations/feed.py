"""Deals a landing page: which titles go in which row, and the order the
rows run in.

Pure logic, like taste.py: every input arrives as an argument, so the rules
can be tested without TMDB, Plex or a browser.

Why this exists. Home, Movies and TV were a stack of rows each fetched on
its own — Trending, Popular, a row per genre, a row per service — and every
one of those lists is ordered by popularity. So they all opened with the
same handful of hits: measured on 2026-09-29, Home put 243 different titles
into 347 slots, and one film sat in five rows. Nothing knew what the rows
above had already shown, and nothing knew who was looking.

This module is the thing that knows both:

**One title, one row.** The page is dealt top to bottom and each title is
used once: a row takes its best titles that nothing above it has shown.
Popular lists overlap by nature, so every row is offered a couple of pages
of candidates and dips into the second when the first is spent.

**Ranked for this person.** Within a row a title is scored on where its own
list put it (so Trending still reads as trending) and on how well it fits
this person — the genres their history leans to, and above all whether the
things they watched, asked for or looked at point to it (TMDB's
recommendations of their seeds). A row's place on the page follows the same
score, so the rows that suit someone come first, with a small daily nudge
so the page isn't identical every morning.

**Nothing they've already seen.** A title they've watched on Plex is left
out of the discovery rows; recommending it back to them is noise.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

# Titles per row, and the fewest worth showing: a row of three reads as a
# bug rather than a short list, so below this it gives its titles back.
ITEMS_PER_ROW = 20
MIN_ITEMS = 6

# How a title's score is made up, once there is a history to score against.
# `position` is where its own list put it: kept strong so each row still
# means what it says. `genre` is fit with the genres this person leans to.
# `suggested` is whether their own seeds' recommendations point at it — the
# most specific signal there is, so it can lift a title well past its
# position.
POSITION_WEIGHT = 0.45
GENRE_WEIGHT = 0.35
SUGGESTED_WEIGHT = 0.8

# How fast a list's own order stops counting: a title at rank 8 has half
# the position score of the first.
POSITION_HALF_RANK = 8.0

# The daily nudge to row order, as a share of the score range. Enough that
# two rows of similar relevance trade places from day to day; too small to
# pull a row nobody cares about above one they do.
ORDER_JITTER = 0.12

# TV and film genres are separate id spaces at TMDB, and several TV ones are
# pairs of film ones ("Action & Adventure" is one TV genre, two film
# genres). Taste is kept in film ids so someone who only watches films still
# has a taste that reaches shows, and the other way round.
_TV_TO_MOVIE_GENRES = {
    10759: (28, 12),  # Action & Adventure
    10765: (878, 14),  # Sci-Fi & Fantasy
    10768: (10752,),  # War & Politics
    10762: (10751,),  # Kids → Family
}


def _film_genres(genre_ids) -> list[int]:
    out: list[int] = []
    for genre_id in genre_ids or []:
        out.extend(_TV_TO_MOVIE_GENRES.get(int(genre_id), (int(genre_id),)))
    return out


def title_key(item: dict, media_type: str) -> tuple[str, int]:
    """A title's identity on the page. Films and shows are separate id
    spaces at TMDB, so the id alone is not enough."""
    return (item.get("media_type") or media_type, int(item["id"]))


@dataclass(frozen=True)
class Taste:
    """What the page is ranked against for one person.

    `genres` and `suggested` are each scaled so their strongest entry is 1.
    `seen` is what they've already watched, left out of the discovery rows.
    """

    genres: dict[int, float] = field(default_factory=dict)
    suggested: dict[tuple[str, int], float] = field(default_factory=dict)
    seen: frozenset[tuple[str, int]] = frozenset()

    @property
    def known(self) -> bool:
        return bool(self.genres or self.suggested)


def _scaled(values: dict) -> dict:
    top = max(values.values(), default=0.0)
    return {key: value / top for key, value in values.items()} if top > 0 else {}


def build_taste(seed_recommendations, seen=()) -> Taste:
    """A taste from each seed's TMDB recommendations.

    `seed_recommendations` is `[(seed, items)]`. Each recommendation counts
    for its seed's weight — so a recent watch outweighs an old request — and
    for less the further down TMDB's list it sits, since its head is a
    firmer pointer than its tail. A title several seeds agree on adds up.
    Genres are read off the same items: they describe this person's taste
    at one remove, which is the right remove for choosing what to show them
    next (see moods.affinity_from_rows).
    """
    genres: dict[int, float] = defaultdict(float)
    suggested: dict[tuple[str, int], float] = defaultdict(float)
    for seed, items in seed_recommendations:
        for rank, item in enumerate(items or []):
            if not item.get("id"):
                continue
            key = (seed.media_type, int(item["id"]))
            if key == (seed.media_type, seed.tmdb_id):
                continue
            weight = seed.weight / (1.0 + rank * 0.1)
            suggested[key] += weight
            for genre_id in _film_genres(item.get("genre_ids")):
                genres[genre_id] += weight
    return Taste(genres=_scaled(genres), suggested=_scaled(suggested), seen=frozenset(seen))


def genre_fit(item: dict, taste: Taste) -> float:
    """How well a title's genres fit this person, 0–1: the mean of its two
    best-fitting genres, so one stray tag neither makes nor breaks it."""
    fits = sorted((taste.genres.get(g, 0.0) for g in _film_genres(item.get("genre_ids"))), reverse=True)
    if not fits:
        return 0.0
    best = fits[:2]
    return sum(best) / len(best)


def score(item: dict, media_type: str, rank: int, taste: Taste) -> float:
    """One title's standing in one row, for one person."""
    position = 1.0 / (1.0 + rank / POSITION_HALF_RANK)
    if not taste.known:
        return position
    return (
        POSITION_WEIGHT * position
        + GENRE_WEIGHT * genre_fit(item, taste)
        + SUGGESTED_WEIGHT * taste.suggested.get(title_key(item, media_type), 0.0)
    )


@dataclass
class Candidate:
    """A row before dealing: everything it could show, in its source's own
    order. `ranked=False` keeps that order as it is — Coming soon is by
    date, and the picks row is already ordered by agreement. `personal`
    rows choose their titles before the rest (see deal)."""

    key: str
    title: str
    qualifier: str
    media_type: str  # "movie" | "tv" | "mixed" — a mixed row's items say which they are
    items: list[dict]
    browse: dict | None = None
    ranked: bool = True
    personal: bool = False


def _ordered(candidate: Candidate, taste: Taste) -> list[tuple[float, dict]]:
    scored = [
        (score(item, candidate.media_type, rank, taste), item)
        for rank, item in enumerate(candidate.items)
        if item.get("id")
    ]
    if candidate.ranked:
        # Stable, so equal scores keep the source's own order.
        scored.sort(key=lambda pair: -pair[0])
    return scored


def relevance(candidate: Candidate, taste: Taste) -> float:
    """How much a row suits this person: the mean score of the first
    screenful it would show. Titles they've seen don't count toward it."""
    shown = [s for s, item in _ordered(candidate, taste) if title_key(item, candidate.media_type) not in taste.seen]
    head = shown[:10]
    return sum(head) / len(head) if head else 0.0


def order_rows(
    pinned: list[str],
    relevances: dict[str, float],
    rng: random.Random,
    jitter: float = ORDER_JITTER,
) -> list[str]:
    """The page's order: pinned rows first as given, then every other row
    by relevance, nudged by today's generator.

    The nudge is drawn per row in a fixed order (sorted keys), so a person
    gets the same page all day and on every device, and a different one
    tomorrow. With no history every relevance is alike and the nudge is
    what orders them — a varied page for someone new, as before.
    """
    nudged = {key: relevances[key] + rng.uniform(0.0, jitter) for key in sorted(relevances)}
    rest = sorted((key for key in nudged if key not in pinned), key=lambda key: -nudged[key])
    return [*pinned, *rest]


def deal(
    order: list[str],
    candidates: dict[str, Candidate],
    taste: Taste,
    claimed: set[tuple[str, int]] | None = None,
    per_row: int = ITEMS_PER_ROW,
    min_items: int = MIN_ITEMS,
) -> dict[str, list[dict]]:
    """Fill the rows, each title used once on the page.

    The rows that explain themselves — "Because you watched Heat", Today's
    top picks — choose first, wherever they sit, then every other row top
    to bottom. Otherwise a named row that happened to land higher could
    take the very titles a "Because you watched" row exists to show and
    leave it too thin to appear. Where rows are displayed doesn't change.

    `claimed` is what's already on the page before any of these rows —
    the Top 10 — and grows as rows are dealt. A row that can't reach
    `min_items` is dropped and gives its titles back, so a thin row never
    starves a better one after it. Rows in `order` with no candidate are
    the frontend's own and are skipped.
    """
    claimed = set(claimed or ())
    dealt: dict[str, list[dict]] = {}
    first = [key for key in order if key in candidates and candidates[key].personal]
    for key in first + [key for key in order if key not in first]:
        candidate = candidates.get(key)
        if candidate is None:
            continue
        picked: list[dict] = []
        taken: list[tuple[str, int]] = []
        for _, item in _ordered(candidate, taste):
            ident = title_key(item, candidate.media_type)
            if ident in claimed or ident in taste.seen or ident in taken:
                continue
            picked.append(item)
            taken.append(ident)
            if len(picked) >= per_row:
                break
        if len(picked) < min_items:
            continue
        claimed.update(taken)
        dealt[key] = picked
    return dealt


def interleave(*lists: list[dict]) -> list[dict]:
    """Several lists as one, alternating — how a Home row mixes films and
    shows so neither half crowds the other out of its first screen."""
    out: list[dict] = []
    for i in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if i < len(items):
                out.append(items[i])
    return out
