"""Two-pass candidate filtering: pass one (relevance gate) decides whether a
search result is actually the title Stage 1 resolved; pass two (quality
score) ranks everything that survives pass one. Same shared token utility
(`app.normalize`) as Stage 1's title-variant matching."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app import config
from app.normalize import normalize_text, tokenize
from app.pipeline_settings import PipelineSettings
from app.resolve import MediaIdentity

_YEAR_TOKEN_RE = re.compile(r"^(19|20)\d{2}$")
_INFOHASH_RE = re.compile(r"btih:([a-zA-Z0-9]+)")
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


# ---------------------------------------------------------------------------
# Plugin/result trust — Stage 0 found rows that populate `fileUrl` while
# actually being an error/config message dressed as a search result.
# ---------------------------------------------------------------------------


def is_trustworthy(result: dict) -> bool:
    engine = (result.get("engineName") or "").strip().lower()
    if engine in config.PLUGIN_DISTRUST:
        return False

    file_url = (result.get("fileUrl") or "").strip()
    if not file_url:
        return False
    if file_url.startswith("magnet:"):
        return True

    host = urlparse(file_url).hostname
    return host is not None and host.lower() not in _LOCAL_HOSTS


# ---------------------------------------------------------------------------
# Pass one: relevance gate
# ---------------------------------------------------------------------------


def _contains_phrase(tokens: list[str], phrase: str) -> bool:
    phrase_tokens = tokenize(phrase)
    if not phrase_tokens:
        return False
    n = len(phrase_tokens)
    return any(tokens[i : i + n] == phrase_tokens for i in range(len(tokens) - n + 1))


def _matches_any_variant(tokens: list[str], identity: MediaIdentity) -> bool:
    return any(_contains_phrase(tokens, variant) for variant in identity.variants)


def _year_within_tolerance(tokens: list[str], release_year: int | None) -> bool:
    if release_year is None:
        return True
    found_years = [int(t) for t in tokens if _YEAR_TOKEN_RE.match(t)]
    if not found_years:
        return True  # no year token present in the filename — nothing to contradict
    return any(abs(year - release_year) <= config.YEAR_TOLERANCE for year in found_years)


def _resolution_score(tokens: list[str]) -> int:
    return _best_tier(tokens, config.RESOLUTION_TIERS)


def _resolution_floor_tier(min_resolution: str) -> int:
    """The tier value for a `min_resolution` setting. An unrecognized
    setting fails safe to the strictest (highest) known tier rather than
    silently admitting everything."""
    target = normalize_text(min_resolution)
    for tier, phrases in config.RESOLUTION_TIERS:
        if any(normalize_text(phrase) == target for phrase in phrases):
            return tier
    return max(tier for tier, _ in config.RESOLUTION_TIERS)


def _passes_resolution_floor(tokens: list[str], min_resolution: str) -> bool:
    """A candidate must carry a *recognized* resolution token at or above
    the configured floor — unrecognized/absent resolution info fails safe
    rather than being guessed at. Any tier above the floor is still fine:
    this is a floor, not a fixed target, so a 2160p release passes a
    "1080p" floor just as a 1080p one does."""
    return _resolution_score(tokens) >= _resolution_floor_tier(min_resolution)


def _passes_language_filter(tokens: list[str], allowlist: tuple[str, ...], blocklist: tuple[str, ...]) -> bool:
    if any(normalize_text(blocked) in tokens for blocked in blocklist):
        return False
    if allowlist:
        return any(normalize_text(allowed) in tokens for allowed in allowlist)
    return True


def _passes_cam_filter(tokens: list[str]) -> bool:
    """Rejects releases explicitly tagged as a cam/telesync/screener rip.
    Doesn't (can't) catch a bootleg that just omits any source tag — see
    config.py's CAM_BLOCKLIST comment."""
    return not any(normalize_text(blocked) in tokens for blocked in config.CAM_BLOCKLIST)


def passes_relevance_gate(file_name: str, identity: MediaIdentity, settings: PipelineSettings | None = None) -> bool:
    settings = settings or PipelineSettings.from_config()
    tokens = tokenize(file_name)
    return (
        _matches_any_variant(tokens, identity)
        and _year_within_tolerance(tokens, identity.release_year)
        and _passes_resolution_floor(tokens, settings.min_resolution)
        and _passes_language_filter(tokens, settings.language_allowlist, settings.language_blocklist)
        and _passes_cam_filter(tokens)
    )


# ---------------------------------------------------------------------------
# Viability gate — size/seeders are unreliable per-field (Stage 0), so
# "unknown" (-1) passes; a known-bad value does not.
# ---------------------------------------------------------------------------


def passes_viability_gate(result: dict, settings: PipelineSettings | None = None) -> bool:
    settings = settings or PipelineSettings.from_config()
    seeders = result.get("nbSeeders", -1)
    if seeders is not None and seeders >= 0 and seeders < config.MIN_SEEDERS:
        return False

    size = result.get("fileSize", -1) or -1
    if size > 0:
        min_bytes = settings.min_size_gb * 1_000_000_000
        max_bytes = settings.max_size_gb * 1_000_000_000
        if not (min_bytes <= size <= max_bytes):
            return False

    return True


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def _infohash(file_url: str) -> str | None:
    match = _INFOHASH_RE.search(file_url or "")
    return match.group(1).lower() if match else None


def dedup_candidates(results: list[dict]) -> list[dict]:
    """Dedup within a result set on infohash (magnet links) or normalized
    name+size (non-magnet fileUrls)."""
    seen: set = set()
    deduped = []
    for result in results:
        key = _infohash(result.get("fileUrl", "")) or (
            normalize_text(result.get("fileName", "")),
            result.get("fileSize"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def exclude_existing(results: list[dict], existing_hashes: set[str]) -> list[dict]:
    """Drops candidates whose infohash is already present in qBittorrent."""
    return [r for r in results if _infohash(r.get("fileUrl", "")) not in existing_hashes]


# ---------------------------------------------------------------------------
# Pass two: quality score
# ---------------------------------------------------------------------------

# Each weight must exceed the maximum possible sum of every lower-priority
# term, so one tier's difference always dominates the next tier down:
# resolution > source > codec > seeder health > container. Seeder health
# sits above container deliberately: a real-world cross-check (Dune: Part
# Two, 2026-09-04) found a 267-seeder REMUX losing to a 10-seeder release
# of the same source/codec tier purely because the loser's filename
# happened to state "MP4" explicitly while the winner's container was
# unstated (near-certainly MKV by REMUX convention, just not spelled out —
# and REMUX audio like TrueHD/Atmos barely fits in MP4 anyway). Container
# format is a much weaker, noisier quality signal than a large swarm-size
# gap, so it's now the one seeder health is allowed to override; codec and
# above still can't be — see config.py's SEEDER_TIERS comment.
_RESOLUTION_WEIGHT = 10000
_SOURCE_WEIGHT = 1000
_CODEC_WEIGHT = 100
_SEEDER_WEIGHT = 10
_CONTAINER_WEIGHT = 1


@dataclass
class Score:
    resolution_score: int
    source_score: int
    codec_score: int
    container_score: int
    seeder_score: int
    composite: int
    size_bytes: int
    seeders: int

    @property
    def sort_key(self) -> tuple:
        # Known-and-healthy beats unknown before a (usually noise-level)
        # size difference gets a say — same real-world case as above: two
        # releases can differ by <0.1% in size for reasons as trivial as a
        # bundled sample file, which shouldn't outweigh "we know this swarm
        # is actually alive."
        seeders_known = 1 if self.seeders >= 0 else 0
        return (self.composite, seeders_known, self.seeders, self.size_bytes)


def _best_tier(tokens: list[str], tiers: tuple) -> int:
    for score, phrases in tiers:
        if any(_contains_phrase(tokens, phrase) for phrase in phrases):
            return score
    return 0


def _seeder_score(seeders: int) -> int:
    if seeders is None or seeders < 0:
        return config.UNKNOWN_SEEDERS_SCORE
    for score, min_seeders in config.SEEDER_TIERS:
        if seeders >= min_seeders:
            return score
    return 0


def score_candidate(result: dict) -> Score:
    tokens = tokenize(result.get("fileName", ""))
    resolution_score = _resolution_score(tokens)
    source_score = _best_tier(tokens, config.SOURCE_TIERS)
    codec_score = _best_tier(tokens, config.CODEC_TIERS)
    container_score = _best_tier(tokens, config.CONTAINER_TIERS)
    size = result.get("fileSize") or 0
    seeders = result.get("nbSeeders", -1)
    seeders = seeders if seeders is not None else -1
    seeder_score = _seeder_score(seeders)
    composite = (
        resolution_score * _RESOLUTION_WEIGHT
        + source_score * _SOURCE_WEIGHT
        + codec_score * _CODEC_WEIGHT
        + seeder_score * _SEEDER_WEIGHT
        + container_score * _CONTAINER_WEIGHT
    )
    return Score(
        resolution_score=resolution_score,
        source_score=source_score,
        codec_score=codec_score,
        container_score=container_score,
        seeder_score=seeder_score,
        composite=composite,
        size_bytes=max(size, 0),
        seeders=seeders,
    )


def rank_candidates(results: list[dict]) -> list[tuple[dict, Score]]:
    """Every candidate paired with its score, best first."""
    scored = [(r, score_candidate(r)) for r in results]
    scored.sort(key=lambda pair: pair[1].sort_key, reverse=True)
    return scored
