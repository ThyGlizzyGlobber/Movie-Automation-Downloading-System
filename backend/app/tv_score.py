"""Stage 10: episode-aware pass-one relevance gate. Mirrors score.py's
movie relevance gate but swaps the year-tolerance check for a whole-token
episode-identity check — an episode's air year can legitimately differ
from the show's first-air year, so year is no signal at all here, while
the season/episode token is a *tighter* identity signal than year ever
was for movies. Resolution floor, language allow/blocklist, and the cam
blocklist are reused unchanged from score.py (`passes_resolution_floor`,
`passes_language_filter`, `passes_cam_filter`) — a release's resolution/
language/bootleg tags mean the same thing whether it's a movie or an
episode. Pass two (quality score) needs no TV-specific version at all:
`score.py`'s `rank_candidates`/`score_candidate` are reused completely
unchanged by pipeline.py's `download_episode()`."""

import re

from app.normalize import normalize_text, tokenize
from app.pipeline_settings import PipelineSettings
from app.score import (
    matches_any_variant,
    passes_cam_filter,
    passes_language_filter,
    passes_non_video_filter,
    passes_resolution_floor,
)
from app.tv_resolve import ShowIdentity

_SEASON_EPISODE_RE = re.compile(r"^s(\d{1,2})e(\d{1,3})$")
_SEASON_ONLY_RE = re.compile(r"^s(\d{1,2})$")
_EPISODE_ONLY_RE = re.compile(r"^e(\d{1,3})$")


def extract_episode_identity(tokens: list[str]) -> tuple[int, int] | None:
    """Pulls a concrete (season, episode) pair out of a filename's own
    tokens, for Stage 13's `media_organizer.organize_pack` — unlike
    `has_episode_token` (checks whether tokens carry one *specific*,
    already-known episode), this discovers whichever episode a pack's
    individual file actually represents, from the file's own name. Same
    two shapes `has_episode_token` recognizes — a contiguous "s01e04"
    token, or adjacent "s01"/"e04" tokens — first match wins; `None` if
    neither shape is present (a sample, .nfo, subtitle sidecar, or any
    other file organize_pack should skip rather than guess at)."""
    for token in tokens:
        match = _SEASON_EPISODE_RE.match(token)
        if match:
            return int(match.group(1)), int(match.group(2))
    for i in range(len(tokens) - 1):
        season_match = _SEASON_ONLY_RE.match(tokens[i])
        episode_match = _EPISODE_ONLY_RE.match(tokens[i + 1])
        if season_match and episode_match:
            return int(season_match.group(1)), int(episode_match.group(1))
    return None


def has_episode_token(tokens: list[str], season: int, episode: int) -> bool:
    """True if `tokens` carries the episode identity as either a single
    contiguous token ("S01E04" -> "s01e04", no separator in the release
    name) or as two adjacent tokens ("S01.E04"/"S01 E04" -> "s01", "e04").
    A solo "s01" token with no episode token adjacent — the season-pack
    shape — matches neither case, which is what rejects season packs here
    rather than accepting them as an episode match (Stage 10's "season
    packs out of scope for v1" decision)."""
    combined = normalize_text(f"s{season:02d}e{episode:02d}")
    if combined in tokens:
        return True
    season_token = normalize_text(f"s{season:02d}")
    episode_token = normalize_text(f"e{episode:02d}")
    return any(tokens[i] == season_token and tokens[i + 1] == episode_token for i in range(len(tokens) - 1))


def passes_episode_relevance_gate(
    file_name: str,
    identity: ShowIdentity,
    season: int,
    episode: int,
    settings: PipelineSettings | None = None,
) -> bool:
    settings = settings or PipelineSettings.from_config()
    tokens = tokenize(file_name)
    return (
        matches_any_variant(tokens, identity.variants)
        and has_episode_token(tokens, season, episode)
        and passes_resolution_floor(tokens, settings.min_resolution)
        and passes_language_filter(tokens, settings.language_allowlist, settings.language_blocklist)
        and passes_cam_filter(tokens)
        and passes_non_video_filter(tokens)
    )
