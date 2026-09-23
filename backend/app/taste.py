"""Per-user recommendation rows — the "Because you watched…" half of Home.

Pure logic, deliberately: every input arrives as an argument and the one
network call is a function passed in. Recommendation quality is a judgement
call that has to be iterated on, and iterating is only cheap while the rules
can be tested without a Plex server, a TMDB key and a signed-in browser.

Two ideas do most of the work here.

**A seed is a title this person chose**, from any source. Watch history and
their own request history both reduce to the same `Seed`, so a third signal
later (ratings, a "not interested" button) is a new feeder and not a new
scoring system. Seeds decay with age, because what someone watched last week
describes them better than what they watched in March — but they decay
rather than expire, so an old favourite stays eligible instead of falling
off a cliff.

**The day is part of the input.** A page whose rows are a pure function of
history is identical every visit until the history changes, which is the
"stale, same things all the time" problem — and the reason the big services
rotate. So the row set is drawn from a *pool* of eligible seeds using a
generator seeded on (person, UTC date): stable all day, different tomorrow,
different per person on the same day. Rotation is deterministic rather than
random because random would reshuffle on every page load, and a row that
moves while you are looking at it is worse than one that never moves.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timezone

# Weight halves every this many days. Long enough that a month-old binge
# still shapes the page, short enough that a recent change of taste shows up
# within a week or two.
HALF_LIFE_DAYS = 45.0

# What each kind of signal is worth before decay. Finishing something is the
# stronger statement — a request is what someone *expected* to like, a watch
# is what they actually sat through. Close together on purpose: in a
# household that requests a lot and watches unevenly, weighting watches far
# higher would hand the page to whoever leaves things playing.
SOURCE_WEIGHTS = {"watched": 1.0, "requested": 0.8}

# Rows shown per day, and the pool they're drawn from. The gap between these
# two numbers *is* the freshness: 4 of 12 means a seed appears roughly every
# third day rather than every day, so the page turns over without ever
# showing something the person has no connection to.
ROWS_PER_DAY = 4
SEED_POOL = 12

# Titles per row. More than a screenful, so the row is worth scrolling.
ITEMS_PER_ROW = 20

# A request row's media_type is finer-grained than TMDB's namespaces: a
# whole show, one episode of it and a season pack are all the same seed,
# because they are all "this person is watching this show".
_MEDIA_TYPES = {"movie": "movie", "tv": "tv", "episode": "tv", "pack": "tv"}


@dataclass(frozen=True)
class Seed:
    """One title this person chose, and how much it should count."""

    tmdb_id: int
    media_type: str  # "movie" | "tv" — TMDB's namespace, not the request's
    title: str
    weight: float
    source: str  # "watched" | "requested"


@dataclass(frozen=True)
class Row:
    """One rendered row. `key` is stable for a given seed so React can keep
    its identity across refetches, while `qualifier` carries the title the
    row is justified by — the "…watched *Dune*" part, which is what makes a
    recommendation legible instead of mysterious."""

    key: str
    title: str
    qualifier: str
    media_type: str
    items: list[dict]


def _decayed(weight: float, when: str | None, now: datetime) -> float:
    """Exponential decay by age. An unparseable or missing timestamp is
    treated as very old rather than dropped: a signal we can't date is still
    a signal, it just shouldn't outrank one we can."""
    if not when:
        return weight * 0.25
    try:
        stamp = datetime.fromisoformat(when)
    except ValueError:
        return weight * 0.25
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age_days = max((now - stamp).total_seconds() / 86400.0, 0.0)
    return weight * (0.5 ** (age_days / HALF_LIFE_DAYS))


def seeds_from_requests(rows, now: datetime) -> list[Seed]:
    """Their own request history as seeds.

    Collapsed to one seed per title, which matters more than it sounds:
    someone who requested twenty episodes of one show has twenty rows in
    `requests` and exactly one opinion. Left uncollapsed, that show would
    crowd every other interest they have out of the pool.
    """
    best: dict[tuple[str, int], Seed] = {}
    for row in rows:
        media_type = _MEDIA_TYPES.get(getattr(row, "media_type", None) or "")
        tmdb_id = getattr(row, "tmdb_id", None)
        if not media_type or not tmdb_id:
            continue
        weight = _decayed(SOURCE_WEIGHTS["requested"], getattr(row, "created_at", None), now)
        key = (media_type, int(tmdb_id))
        existing = best.get(key)
        # Keep the strongest (so: most recent) of a title's rows, and take
        # the title text from that same row rather than the first seen.
        if existing is None or weight > existing.weight:
            best[key] = Seed(
                tmdb_id=int(tmdb_id),
                media_type=media_type,
                title=getattr(row, "title", None) or "",
                weight=weight,
                source="requested",
            )
    return sorted(best.values(), key=lambda s: s.weight, reverse=True)


def merge_seeds(*groups: list[Seed]) -> list[Seed]:
    """Combine feeders, keeping the strongest claim on each title.

    Summed rather than max'd: something a person both requested *and*
    watched is a stronger signal than either alone, and that is exactly the
    title their page should lean on.
    """
    merged: dict[tuple[str, int], Seed] = {}
    for group in groups:
        for seed in group:
            key = (seed.media_type, seed.tmdb_id)
            existing = merged.get(key)
            if existing is None:
                merged[key] = seed
            else:
                merged[key] = Seed(
                    tmdb_id=seed.tmdb_id,
                    media_type=seed.media_type,
                    # Prefer a non-empty title from either side.
                    title=existing.title or seed.title,
                    weight=existing.weight + seed.weight,
                    # Watched outranks requested as the stated reason, since
                    # "because you watched" is the more honest sentence.
                    source="watched" if "watched" in (existing.source, seed.source) else existing.source,
                )
    return sorted(merged.values(), key=lambda s: s.weight, reverse=True)


def daily_rng(plex_user_id: str, day: str, purpose: str = "") -> random.Random:
    """A generator that is fixed for one person for one day.

    Hashed rather than using `hash()` — that is salted per process in
    Python, so rows would change every time the backend restarted, which is
    the opposite of the point. sha256 of "<person>:<date>" gives the same
    stream on every process, every worker, every restart.

    `purpose` separates decisions that shouldn't move together. Drawing the
    seeds and ordering the page from one stream would couple them: changing
    ROWS_PER_DAY would silently redeal the layout too, and a day where the
    seed draw happened to be short would shift every row under it. Each
    decision gets its own stream, so each can be reasoned about alone.
    """
    key = f"{plex_user_id}:{day}:{purpose}" if purpose else f"{plex_user_id}:{day}"
    digest = hashlib.sha256(key.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def order_rows(pinned: list[str], rotating: list[str], rng: random.Random) -> list[str]:
    """The order rows appear down the page, dealt fresh each day.

    Which personalised rows appear, and their order among themselves, moves
    already — that falls out of sampling the seed pool. What this adds is
    the rest of the page: without it, Popular movies is forever above
    Popular TV which is forever above Coming soon, and the personalised
    rows sit in one fixed slot no matter what they contain.

    `pinned` keeps its order at the top and never moves, because a couple
    of rows earn their place by being navigation rather than browsing:
    Continue watching is how you resume the thing you were in the middle
    of, and hunting for it is a worse experience than any amount of
    freshness is worth. Everything else is dealt.

    Deals the whole list rather than nudging it, deliberately: a gentle
    shuffle produces a page that looks the same at a glance while being
    subtly different, which is the worst of both — it reads as stale *and*
    you can't find anything twice.
    """
    dealt = list(rotating)
    rng.shuffle(dealt)
    return [*pinned, *dealt]


def pick_seeds(seeds: list[Seed], rng: random.Random, count: int = ROWS_PER_DAY, pool: int = SEED_POOL) -> list[Seed]:
    """Today's seeds: a sample of the strongest, not simply the strongest.

    Taking the top `count` by weight would be defensible and completely
    static — the same four rows until the person's history moved. Sampling
    `count` out of the top `pool` keeps every choice well-justified while
    letting the page turn over daily, which is the actual request.
    """
    eligible = seeds[:pool]
    if len(eligible) <= count:
        return eligible
    return rng.sample(eligible, count)


def rotate(items: list[dict], rng: random.Random, size: int = ITEMS_PER_ROW) -> list[dict]:
    """A window onto the recommendations, starting somewhere different each
    day and wrapping.

    A window rather than a shuffle: TMDB returns these in relevance order,
    and shuffling would throw that away to achieve the same freshness. This
    keeps neighbours together and still shows a different part of the list
    tomorrow.
    """
    if not items:
        return []
    if len(items) <= size:
        return items
    start = rng.randrange(len(items))
    return [items[(start + i) % len(items)] for i in range(size)]


def build_rows(
    seeds: list[Seed],
    recommend,
    rng: random.Random,
    exclude: set[tuple[str, int]] | None = None,
    items_per_row: int = ITEMS_PER_ROW,
) -> list[Row]:
    """Turn today's seeds into rows.

    `recommend(media_type, tmdb_id) -> list[dict]` is injected so this stays
    testable and so a failing lookup is the caller's problem to degrade: one
    unavailable seed costs its row, never the page.

    `exclude` is everything this person has already watched or asked for.
    Recommending someone the film they requested last week is the single
    most obvious way for this feature to look broken, and it is exactly what
    TMDB will do, since co-watch data has no idea what is already in your
    library.
    """
    exclude = exclude or set()
    rows: list[Row] = []
    for seed in seeds:
        if not seed.title:
            continue
        try:
            candidates = recommend(seed.media_type, seed.tmdb_id) or []
        except Exception:  # noqa: BLE001 — a dead seed costs its row, not the page
            continue
        fresh = [
            item
            for item in candidates
            if item.get("id") and (seed.media_type, int(item["id"])) not in exclude
        ]
        picked = rotate(fresh, rng, items_per_row)
        # A row of two is worse than no row: it reads as a bug rather than a
        # short list. Below a handful, drop it and let the next seed speak.
        if len(picked) < 4:
            continue
        rows.append(
            Row(
                key=f"because:{seed.media_type}:{seed.tmdb_id}",
                title="Because you watched" if seed.source == "watched" else "Because you asked for",
                qualifier=seed.title,
                media_type=seed.media_type,
                items=picked,
            )
        )
    return rows


def today(now: datetime | None = None) -> str:
    """The rotation's clock, in UTC.

    UTC rather than local: the rows should turn over at one moment for the
    whole household, not drift with whatever timezone a phone happens to
    report.
    """
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
