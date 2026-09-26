"""Stage 13 (+ Stage 14.x's season-range gate): pass-one gates for a
season/range/series-*shaped* result — the deliberate mirror image of
tv_score.py's per-episode gate. tv_score.py's `passes_episode_relevance_gate`
rejects a lone season token with no adjacent episode token (Stage 10's
"season packs out of scope for v1" call, still true today for the
per-episode auto-grab loop); this module accepts three pack shapes instead
— one season (`passes_season_pack_gate`), an explicit multi-season range
(`passes_season_range_pack_gate`), or the complete series
(`passes_series_pack_gate`) — never in a way that could let a pack sneak
past the per-episode gate, since the two gates are wired to completely
separate call paths (`pipeline.download_episode` vs `pipeline.download_pack`).
Used both by an explicit user-triggered bulk download (api.py's
`POST /api/shows/{id}/bulk-download`) and, as of Stage 14.x,
`worker.check_show()`'s own automatic pack preference for a season/show
that's already finished airing — no longer manual-only.

Reuses score.py's resolution floor / language filter / cam blocklist and
`matches_any_variant`/`contains_phrase` completely unchanged — a
release's quality/language/bootleg signals mean the same thing whether
it's a movie, an episode, or a pack."""

import re

from app import config
from app.normalize import normalize_text, tokenize
from app.pipeline_settings import PipelineSettings
from app.score import (
    contains_phrase,
    matches_any_variant,
    passes_cam_filter,
    passes_language_filter,
    passes_non_video_filter,
    passes_resolution_floor,
)
from app.tv_resolve import ShowIdentity

# Recognizes a concrete episode marker — either the contiguous "s01e04"
# shape or a lone "e04" half of the dotted/spaced "s01"+"e04" shape — so a
# result that's superficially season-shaped ("S01") but actually names one
# specific episode ("S01E04") is correctly kept out of the pack gates below,
# not double-counted as both an episode match and a pack match.
_EPISODE_TOKEN_RE = re.compile(r"^s\d{1,2}e\d{1,3}$")
_LONE_EPISODE_RE = re.compile(r"^e\d{1,3}$")

# Real-world phrasing for a whole-series release. Deliberately a short,
# explicit list rather than a heuristic ("many season tokens at once").
_COMPLETE_SERIES_PHRASES = (
    "complete series",
    "the complete series",
    "complete collection",
    "complete seasons",
    "all seasons",
    "full series",
    "entire series",
)

# A season number as it appears loose in a name ("Season 1 2 3", "S01-03"
# once the dash is a boundary): one or two digits, never a year.
_SEASON_NUMBER_RE = re.compile(r"^\d{1,2}$")
_RANGE_FILLER = ("to", "and", "thru", "through")

# A bare season token, same shape has_season_token already checks for one
# specific season — used here to find *two* adjacent ones, the "S01-S03"
# shape once tokenize() has normalized the hyphen away to whitespace (the
# same whole-token treatment every other separator gets in this codebase).
_SEASON_TOKEN_RE = re.compile(r"^s(\d{1,2})$")


def has_any_episode_token(tokens: list[str]) -> bool:
    """True if any token looks like an episode marker at all, for *any*
    episode number — looser than tv_score.has_episode_token (which checks
    one specific episode), and used here for the opposite purpose: keeping
    a genuine single-episode release out of the season-pack gate, rather
    than confirming a specific episode's identity."""
    return any(_EPISODE_TOKEN_RE.match(t) or _LONE_EPISODE_RE.match(t) for t in tokens)


def has_season_token(tokens: list[str], season: int) -> bool:
    season_token = normalize_text(f"s{season:02d}")
    return (
        season_token in tokens
        or contains_phrase(tokens, f"season {season}")
        or contains_phrase(tokens, f"season {season:02d}")
    )


def has_complete_series_marker(tokens: list[str]) -> bool:
    return any(contains_phrase(tokens, phrase) for phrase in _COMPLETE_SERIES_PHRASES)


def _is_bare_complete(tokens: list[str]) -> bool:
    """"<Show> - Complete": the word on its own, with no season number,
    range or episode marker anywhere in the name to narrow it. A name
    like "Seasn 2 complete" (typo and all) still carries a loose season
    number, so it stays out."""
    if "complete" not in tokens:
        return False
    if has_any_episode_token(tokens):
        return False
    if any(_SEASON_TOKEN_RE.match(t) or _SEASON_NUMBER_RE.match(t) for t in tokens):
        return False
    return "season" not in tokens and "seasons" not in tokens


def passes_season_pack_gate(
    file_name: str, identity: ShowIdentity, season: int, settings: PipelineSettings | None = None
) -> bool:
    """Accepts a result carrying `season`'s season token with **no**
    accompanying episode token — the exact shape Stage 10's per-episode
    gate rejects, deliberately reversed here since a bulk season download
    *wants* a pack, not a single episode."""
    settings = settings or PipelineSettings.from_config()
    tokens = tokenize(file_name)
    return (
        matches_any_variant(
            tokens, identity.variants, identity.first_air_year, config.PACK_YEAR_TOLERANCE
        )
        and has_season_token(tokens, season)
        and not has_any_episode_token(tokens)
        and passes_resolution_floor(tokens, settings.min_resolution)
        and passes_language_filter(
            tokens, settings.language_allowlist, settings.language_blocklist, settings.language_required
        )
        and passes_cam_filter(tokens)
        and passes_non_video_filter(tokens)
    )


def parse_season_range(tokens: list[str]) -> tuple[int, int] | None:
    """Finds an explicit multi-season range marker in a tokenized
    filename — either two adjacent bare season tokens ('s01', 's03': the
    "S01-S03" shape, the hyphen already normalized to a token boundary
    like every other separator here) or the word "season"/"seasons"
    immediately followed by two adjacent plain numbers ("season 1 3": the
    "Season 1-3" shape). Returns `(start, end)` with `start < end`, or
    `None` if no such marker is found — deliberately conservative: a lone
    "s01" with no adjacent second season token is `passes_season_pack_gate`'s
    shape, not this one's, and this never infers a range from anything
    softer than an explicit marker naming real season numbers."""
    for i in range(len(tokens) - 1):
        first = _SEASON_TOKEN_RE.match(tokens[i])
        if not first:
            continue
        # "S01-S03", or the shorter "S01-03" (the dash is already a boundary).
        second = _SEASON_TOKEN_RE.match(tokens[i + 1])
        end_text = second.group(1) if second else (tokens[i + 1] if _SEASON_NUMBER_RE.match(tokens[i + 1]) else None)
        if end_text is not None:
            start, end = int(first.group(1)), int(end_text)
            if start < end:
                return start, end
    for i, token in enumerate(tokens):
        if token not in ("season", "seasons"):
            continue
        # "Seasons 1-3", "Seasons 1 to 3", "Season 1 2 3": a run of loose
        # season numbers (years are three digits too long to count).
        numbers: list[int] = []
        for later in tokens[i + 1 :]:
            if _SEASON_NUMBER_RE.match(later):
                numbers.append(int(later))
            elif later in _RANGE_FILLER:
                continue
            else:
                break
        if len(numbers) >= 2 and numbers[0] < numbers[-1]:
            return numbers[0], numbers[-1]
    return None


def has_season_range_marker(tokens: list[str], start: int, end: int) -> bool:
    """True if the tokens carry an explicit season-range marker whose own
    claimed range fully covers `[start, end]`. A release bundling *more*
    than what was actually needed (tagged "S01-S04" when only seasons 1-3
    were unhandled) still satisfies the request — `organize_pack()` only
    ever files episodes it actually finds, and the ledger already skips
    re-tracking anything already handled, so there's no harm in a
    slightly wider real release covering the gap."""
    found = parse_season_range(tokens)
    if found is None:
        return False
    found_start, found_end = found
    return found_start <= start and found_end >= end


def passes_season_range_pack_gate(
    file_name: str, identity: ShowIdentity, start: int, end: int, settings: PipelineSettings | None = None
) -> bool:
    """Accepts a result whose filename carries an explicit multi-season
    range marker covering at least `[start, end]`, with no episode token
    of its own — the shape a bundled "S01-S03" release takes while a show
    is still airing its next season. Distinct from both siblings here:
    unlike `passes_season_pack_gate` (one fixed season) and
    `passes_series_pack_gate` (one fixed phrase), an arbitrary partial
    range has no single fixed naming convention, so this gate requires an
    *explicit* marker naming real season numbers rather than inferring a
    range from anything softer (e.g. file size, or "no episode token" the
    way the single-season gate's absence check works)."""
    settings = settings or PipelineSettings.from_config()
    tokens = tokenize(file_name)
    return (
        matches_any_variant(
            tokens, identity.variants, identity.first_air_year, config.PACK_YEAR_TOLERANCE
        )
        and has_season_range_marker(tokens, start, end)
        and not has_any_episode_token(tokens)
        and passes_resolution_floor(tokens, settings.min_resolution)
        and passes_language_filter(
            tokens, settings.language_allowlist, settings.language_blocklist, settings.language_required
        )
        and passes_cam_filter(tokens)
        and passes_non_video_filter(tokens)
    )


def is_series_shaped(tokens: list[str], number_of_seasons: int | None, finished_seasons: int | None = None) -> bool:
    """A positive whole-series signal, in one of three real-world shapes:
    an explicit phrase ("Complete Series", "All Seasons"…); a season range
    that spans every season the show has ("S01-S03", "Complete Seasons 1
    to 3" for a three-season show — which needs `number_of_seasons`) or,
    while the last season is still airing, every *finished* season
    (`finished_seasons`); or the bare word "Complete" with nothing
    narrowing it. An explicit range always has the last word: "Complete
    Seasons 1 to 2" of a finished three-season show is not the whole
    show, however it's phrased."""
    found = parse_season_range(tokens)
    if found is not None:
        if number_of_seasons is None:
            return has_complete_series_marker(tokens)
        if has_any_episode_token(tokens) or found[0] > 1:
            return False
        if found[1] >= number_of_seasons:
            return True
        return finished_seasons is not None and 0 < finished_seasons < number_of_seasons and found[1] >= finished_seasons
    return has_complete_series_marker(tokens) or _is_bare_complete(tokens)


def passes_series_pack_gate(
    file_name: str, identity: ShowIdentity, settings: PipelineSettings | None = None
) -> bool:
    """Accepts only a positive whole-series signal (`is_series_shaped`),
    never inferring "series-shaped" from the mere absence of an episode
    token (which a single stray file, or this function's own season-pack
    sibling, would also satisfy)."""
    settings = settings or PipelineSettings.from_config()
    tokens = tokenize(file_name)
    return (
        matches_any_variant(
            tokens, identity.variants, identity.first_air_year, config.PACK_YEAR_TOLERANCE
        )
        and is_series_shaped(tokens, identity.number_of_seasons, identity.finished_seasons)
        and passes_resolution_floor(tokens, settings.min_resolution)
        and passes_language_filter(
            tokens, settings.language_allowlist, settings.language_blocklist, settings.language_required
        )
        and passes_cam_filter(tokens)
        and passes_non_video_filter(tokens)
    )
