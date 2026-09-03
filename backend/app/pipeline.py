"""Search -> filter -> score -> select -> add. Plain Python, no FastAPI
dependency — the API (Stage 3) is a thin wrapper around `download()`, which
is what makes this CLI-testable."""

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

        return DownloadResult(
            status="added",
            identity=identity,
            variant_used=variant,
            winner=winner,
            score=winner_score,
            candidates_considered=len(candidates),
        )

    status = "insufficient free space" if any_candidates else "no qualifying results"
    return DownloadResult(status=status, identity=identity)
