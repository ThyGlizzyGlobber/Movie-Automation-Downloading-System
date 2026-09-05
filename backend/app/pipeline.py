"""Search -> filter -> score -> select -> add. Plain Python, no FastAPI
dependency — the API (Stage 3) is a thin wrapper around `download()`, which
is what makes this CLI-testable."""

import time
from dataclasses import dataclass

from app import config
from app.pipeline_settings import PipelineSettings
from app.qbt import QBTClient, QBTError
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
    status: str  # "added" | "no qualifying results" | "insufficient free space" | "add failed"
    identity: MediaIdentity
    variant_used: str | None = None
    winner: dict | None = None
    score: Score | None = None
    candidates_considered: int = 0
    torrent_hash: str | None = None
    error: str | None = None  # populated only for "add failed"


def _search_variant(
    qbt: QBTClient, variant: str, identity: MediaIdentity, existing_hashes: set[str], settings: PipelineSettings
) -> list[dict]:
    raw_results = qbt.search(variant, category=settings.category)
    trustworthy = [r for r in raw_results if is_trustworthy(r)]
    relevant = [r for r in trustworthy if passes_relevance_gate(r.get("fileName", ""), identity, settings)]
    viable = [r for r in relevant if passes_viability_gate(r, settings)]
    deduped = dedup_candidates(viable)
    return exclude_existing(deduped, existing_hashes)


def _candidates_that_fit(ranked: list[tuple[dict, Score]], free_space_bytes: int) -> list[tuple[dict, Score]]:
    """Every candidate (best-ranked first) whose size is known to fit in
    the space qBittorrent reports free — not just the top one, so a failed
    add (see below) has somewhere to fall back to. An unreadable free-space
    figure (<=0) doesn't gate — nothing in Stage 0/1 suggested that ever
    happens against the real NAS, but a mocked/broken read shouldn't
    silently block every download either."""
    return [
        (candidate, score)
        for candidate, score in ranked
        if free_space_bytes <= 0 or score.size_bytes == 0 or score.size_bytes <= free_space_bytes
    ]


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
    was consistently still missing on the first check.

    This is also the *real* arbiter of whether the add actually happened at
    all — `qbt.add_torrent()`'s own synchronous response can't tell a
    result that's merely still indexing from one that will never land
    (confirmed live: qBittorrent's WebAPI 2.14+ metadata response reports a
    still-fetching direct-.torrent-URL as "pending", not "success" or
    "failure", the instant the call returns). If zero new hashes ever show
    up despite exhausting every retry, nothing was actually added — raises
    QBTError rather than reporting "added" with an untracked, nonexistent
    torrent. The ambiguous case (more than one new hash) is different:
    something was clearly added, so that still returns None as before."""
    saw_any_new_hash = False
    for _ in range(config.HASH_CAPTURE_ATTEMPTS):
        hashes_after = qbt.existing_torrent_hashes()
        new_hashes = hashes_after - hashes_before
        if len(new_hashes) == 1:
            return next(iter(new_hashes))
        if len(new_hashes) > 1:
            saw_any_new_hash = True
            break
        time.sleep(config.HASH_CAPTURE_INTERVAL_SECONDS)
    if not saw_any_new_hash:
        raise QBTError("qBittorrent never actually added this torrent (still absent after checking several times)")
    return None


def download(
    tmdb_id: int, tmdb_client: TMDBClient, qbt: QBTClient, settings: PipelineSettings | None = None
) -> DownloadResult:
    settings = settings or PipelineSettings.from_config()
    identity = resolve(tmdb_id, tmdb_client)
    existing_hashes = qbt.existing_torrent_hashes()
    free_space_bytes = qbt.free_space_bytes()
    any_candidates = False
    # Real-world case that motivated this: a scraper (limetorrents) listing
    # a link qBittorrent could never actually fetch — the #1-ranked
    # candidate failing to add used to fail the whole request outright,
    # even with 79 other candidates still on the table. Keeps the most
    # recent failed attempt (variant/winner/score/candidates_considered/
    # error) so a total failure still carries a full audit trail, same as
    # a success would — "hidden, never unrecoverable".
    last_failed_attempt: DownloadResult | None = None

    for variant in identity.variants:
        candidates = _search_variant(qbt, variant, identity, existing_hashes, settings)
        if not candidates:
            continue
        any_candidates = True

        ranked = rank_candidates(candidates)
        fitting = _candidates_that_fit(ranked, free_space_bytes)
        if not fitting:
            continue  # every candidate for this variant is too large for available space

        for winner, winner_score in fitting:
            qbt.ensure_category(settings.category)
            try:
                qbt.add_torrent(winner["fileUrl"], category=settings.category)
                torrent_hash = _capture_new_hash(qbt, existing_hashes)
            except QBTError as exc:
                # This candidate never actually landed in qBittorrent (a
                # dead/unreachable link, most commonly) — try the next-best
                # one instead of giving up on the whole request.
                last_failed_attempt = DownloadResult(
                    status="add failed",
                    identity=identity,
                    variant_used=variant,
                    winner=winner,
                    score=winner_score,
                    candidates_considered=len(candidates),
                    error=str(exc),
                )
                continue

            return DownloadResult(
                status="added",
                identity=identity,
                variant_used=variant,
                winner=winner,
                score=winner_score,
                candidates_considered=len(candidates),
                torrent_hash=torrent_hash,
            )

    if last_failed_attempt is not None:
        return last_failed_attempt

    status = "insufficient free space" if any_candidates else "no qualifying results"
    return DownloadResult(status=status, identity=identity)
