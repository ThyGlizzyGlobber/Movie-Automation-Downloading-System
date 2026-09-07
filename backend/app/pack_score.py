"""Stage 13: pass-one gates for a season-or-series-*shaped* result — the
deliberate mirror image of tv_score.py's per-episode gate. tv_score.py's
`passes_episode_relevance_gate` rejects a lone season token with no
adjacent episode token (Stage 10's "season packs out of scope for v1"
call, still true today for the per-episode auto-grab loop); this module
accepts that exact shape instead, but only when a caller has explicitly
asked for a bulk pack download (api.py's `POST /api/shows/{id}/bulk-
download`) — never automatically, and never in a way that could let a
pack sneak past the per-episode gate, since the two gates are wired to
completely separate call paths (`pipeline.download_episode` vs
`pipeline.download_pack`).

Reuses score.py's resolution floor / language filter / cam blocklist and
`matches_any_variant`/`contains_phrase` completely unchanged — a
release's quality/language/bootleg signals mean the same thing whether
it's a movie, an episode, or a pack."""

import re

from app.normalize import normalize_text, tokenize
from app.pipeline_settings import PipelineSettings
from app.score import (
    contains_phrase,
    matches_any_variant,
    passes_cam_filter,
    passes_language_filter,
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
# explicit list rather than a heuristic ("many season tokens at once") —
# see project.md's Stage 13 open decisions on why a partial season range
# isn't reliably distinguishable from a genuine complete series in v1.
_COMPLETE_SERIES_PHRASES = ("complete series", "the complete series", "complete collection")


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
        matches_any_variant(tokens, identity.variants)
        and has_season_token(tokens, season)
        and not has_any_episode_token(tokens)
        and passes_resolution_floor(tokens, settings.min_resolution)
        and passes_language_filter(tokens, settings.language_allowlist, settings.language_blocklist)
        and passes_cam_filter(tokens)
    )


def passes_series_pack_gate(
    file_name: str, identity: ShowIdentity, settings: PipelineSettings | None = None
) -> bool:
    """Accepts only an explicit complete-series marker — a real, positive
    signal, rather than inferring "series-shaped" from the mere absence of
    an episode token (which a single stray file, or this function's own
    season-pack sibling, would also satisfy)."""
    settings = settings or PipelineSettings.from_config()
    tokens = tokenize(file_name)
    return (
        matches_any_variant(tokens, identity.variants)
        and has_complete_series_marker(tokens)
        and passes_resolution_floor(tokens, settings.min_resolution)
        and passes_language_filter(tokens, settings.language_allowlist, settings.language_blocklist)
        and passes_cam_filter(tokens)
    )
