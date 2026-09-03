"""Search -> filter -> score -> select -> add. Plain Python, no FastAPI
dependency — the API (Stage 3) is a thin wrapper around `download()`, which
is what makes this CLI-testable."""

import time
from dataclasses import dataclass

from app import config
from app.qbt import QBTClient
from app.resolve import MediaIdentity, resolve
from app.score import (
    Score,
    dedup_candidates,
    exclude_existing,
    is_trustworthy,
    passes_relevance_gate,
    passes_viability_gate,
    rank_candidates,
)
from app.tmdb import TMDBClient


@dataclass
class DownloadResult:
    status: str  # "added" | "no qualifying results" | "insufficient free space"
    identity: MediaIdentity
    variant_used: str | None = None
    winner: dict | None = None
    score: Score | None = None
    candidates_considered: int = 0
    torrent_hash: str | None = None


def _search_variant(qbt: QBTClient, variant: str, identity: MediaIdentity, existing_hashes: set[str]) -> list[dict]:
    raw_results = qbt.search(variant, category=config.CATEGORY)
    trustworthy = [r for r in raw_results if is_trustworthy(r)]
    relevant = [r for r in trustworthy if passes_relevance_gate(r.get("fileName", ""), identity)]
    viable = [r for r in relevant if passes_viability_gate(r)]
    deduped = dedup_candidates(viable)
    return exclude_existing(deduped, existing_hashes)


def _best_that_fits(ranked: list[tuple[dict, Score]], free_space_bytes: int) -> tuple[dict, Score] | None:
    """First (best-ranked) candidate whose size is known to fit in the
    space qBittorrent reports free. An unreadable free-space figure (<=0)
    doesn't gate — nothing in Stage 0/1 suggested that ever happens against
    the real NAS, but a mocked/broken read shouldn't silently block every
    download either."""
    for candidate, score in ranked:
        if free_space_bytes <= 0 or score.size_bytes == 0 or score.size_bytes <= free_space_bytes:
            return candidate, score
    return None


def _capture_new_hash(qbt: QBTClient, hashes_before: set[str]) -> str | None:
    """Stage 3 needs a way to track *this specific* add through to
    completion (the API's "downloading" -> "complete" transition). Works
    for both magnet and direct-.torrent-URL results, unlike parsing
    `fileUrl` (only magnets carry an infohash). Ambiguous (more than one new
    hash — e.g. a concurrent manual add) fails safe to untracked rather than
    guessing which one is ours.

    Retries briefly: a magnet is indexed by qBittorrent essentially
    instantly, but a direct-.torrent-URL result (the majority of real
    winners, per Stage 0/2's live sample — torlock etc.) isn't visible in
    `existing_torrent_hashes()` until qBittorrent has actually fetched and
    parsed it, which `torrents_add()` doesn't wait for. Confirmed live
    against the real NAS during Stage 3 validation: a torlock winner's hash
    was consistently still missing on the first check."""
    for _ in range(config.HASH_CAPTURE_ATTEMPTS):
        hashes_after = qbt.existing_torrent_hashes()
        new_hashes = hashes_after - hashes_before
        if len(new_hashes) == 1:
            return next(iter(new_hashes))
        if len(new_hashes) > 1:
            return None
        time.sleep(config.HASH_CAPTURE_INTERVAL_SECONDS)
    return None


def download(tmdb_id: int, tmdb_client: TMDBClient, qbt: QBTClient) -> DownloadResult:
    identity = resolve(tmdb_id, tmdb_client)
    existing_hashes = qbt.existing_torrent_hashes()
    free_space_bytes = qbt.free_space_bytes()
    any_candidates = False

    for variant in identity.variants:
        candidates = _search_variant(qbt, variant, identity, existing_hashes)
        if not candidates:
            continue
        any_candidates = True

        ranked = rank_candidates(candidates)
        fit = _best_that_fits(ranked, free_space_bytes)
        if fit is None:
            continue  # every candidate for this variant is too large for available space
        winner, winner_score = fit

        qbt.ensure_category(config.CATEGORY)
        qbt.add_torrent(winner["fileUrl"], category=config.CATEGORY)
        torrent_hash = _capture_new_hash(qbt, existing_hashes)

        return DownloadResult(
            status="added",
            identity=identity,
            variant_used=variant,
            winner=winner,
            score=winner_score,
            candidates_considered=len(candidates),
            torrent_hash=torrent_hash,
        )

    status = "insufficient free space" if any_candidates else "no qualifying results"
    return DownloadResult(status=status, identity=identity)
