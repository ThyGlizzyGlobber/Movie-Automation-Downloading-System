"""Search -> filter -> score -> select -> add. Plain Python, no FastAPI
dependency — the API (Stage 3) is a thin wrapper around `download()`, which
is what makes this CLI-testable."""

import threading
import time
from dataclasses import dataclass

from app import config
from app.normalize import tokenize
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
    states_resolution,
)
from app.pack_score import passes_season_pack_gate, passes_season_range_pack_gate, passes_series_pack_gate
from app.tmdb import TMDBClient
from app.tv_resolve import (
    ShowIdentity,
    episode_query,
    season_pack_queries,
    season_range_pack_queries,
    series_pack_queries,
)
from app.tv_score import passes_episode_relevance_gate

# One add at a time, process-wide — see _rank_and_add. A module-level
# lock rather than one passed in: there is a single backend process (no
# multi-worker deployment), the same premise the in-memory rate limiter
# and job queue already rest on.
_ADD_LOCK = threading.Lock()


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


@dataclass
class EpisodeDownloadResult:
    status: str  # same vocabulary as DownloadResult
    identity: ShowIdentity
    season: int
    episode: int
    variant_used: str | None = None
    winner: dict | None = None
    score: Score | None = None
    candidates_considered: int = 0
    torrent_hash: str | None = None
    error: str | None = None


@dataclass
class PackDownloadResult:
    """Stage 13: one season- or complete-series-pack add attempt. Unlike
    `EpisodeDownloadResult`, this carries no single `season`/`episode` —
    which episodes a pack actually turns out to contain is only knowable
    once its file list is inspected post-download (`media_organizer.
    organize_pack`), not at search/add time."""

    status: str  # same vocabulary as DownloadResult/EpisodeDownloadResult
    identity: ShowIdentity
    scope: str  # "season" | "season_range" | "series"
    season: int | None = None  # the range's start, when scope == "season_range"
    season_range_end: int | None = None  # only set when scope == "season_range"
    variant_used: str | None = None
    query_used: str | None = None
    winner: dict | None = None
    score: Score | None = None
    candidates_considered: int = 0
    torrent_hash: str | None = None
    error: str | None = None
    # Why a "no qualifying results" was chosen on purpose (a series pack
    # was found, but the seasons are available at a higher quality).
    note: str | None = None


def _search_variant(
    qbt: QBTClient,
    variant: str,
    identity: MediaIdentity,
    existing_hashes: set[str],
    settings: PipelineSettings,
    excluded_releases: list[dict] | None = None,
) -> list[dict]:
    # Search unscoped ("all"), not `settings.category` — confirmed live
    # (Stage 12's real Lanterns S01E03 validation) that at least one real,
    # enabled plugin (sktorrent) simply returns zero results when qBittorrent
    # asks it to filter to a specific category, silently hiding otherwise-
    # qualifying releases, even though the same query against "all"
    # categories finds them. `settings.category` is still exactly what gets
    # applied to the torrent once added (see `_rank_and_add` below) — this
    # only changes what's asked for at *search* time; our own relevance
    # gate (title/year/token matching) is what actually decides relevance,
    # not qBittorrent's per-plugin category tagging, which turned out to be
    # an unreliable pre-filter to trust.
    raw_results = qbt.search(variant, category="all")
    trustworthy = [r for r in raw_results if is_trustworthy(r)]
    relevant = [r for r in trustworthy if passes_relevance_gate(r.get("fileName", ""), identity, settings)]
    if excluded_releases:
        # A copy rejected without a hash (this app never added it) is
        # skipped however it's listed — matched by size, or by a name
        # that still says which release it was.
        relevant = [r for r in relevant if not any(_is_rejected_release(r, rej) for rej in excluded_releases)]
    viable = [r for r in relevant if passes_viability_gate(r, settings)]
    deduped = dedup_candidates(viable)
    return exclude_existing(deduped, existing_hashes)


def _merge_variant_candidates(variants: list[str], search_one_variant) -> list[dict]:
    """Searches every variant — not stopping at the first that finds
    anything — and returns the combined, deduped candidate pool, each
    candidate tagged with which variant's search actually found it
    (`_variant_used`, read back off the eventual winner for the result's
    own audit trail).

    Confirmed live (2026-09-14): stopping at the first variant with any
    result at all missed a real 2160p release. "Special Ops: Lioness"
    S01 existed at 2160p on this household's own trackers, but the show's
    official title search (and the old, backwards subtitle-split fallback
    "Special Ops" — see normalize.py's `generate_variants`) never actually
    found it, while a plain "Lioness" search easily did. Different
    variants' literal query text can surface genuinely different,
    non-overlapping subsets of the same real release pool on a given
    indexer/plugin — only combining every variant's results before
    ranking reliably finds the actual best available quality, not just
    whatever the first variant that found *anything* happened to turn
    up. The real cost: up to `len(variants)` sequential search calls
    (each up to `SEARCH_CEILING_SECONDS`) instead of stopping early — a
    deliberate trade of slower per-request search time for not silently
    settling on worse quality."""
    combined: list[dict] = []
    for variant in variants:
        for candidate in search_one_variant(variant):
            tagged = dict(candidate)
            tagged["_variant_used"] = variant
            combined.append(tagged)
    return dedup_candidates(combined)


def usable_free_space(qbt: QBTClient, settings: "PipelineSettings | None") -> int:
    """qBittorrent's free space minus the configured floor. An unreadable
    figure (<=0) stays <=0 so the fit check below keeps not gating on
    it; a readable figure below the floor becomes 1 byte, which nothing
    with a known size fits into."""
    free = qbt.free_space_bytes()
    floor = int((settings.free_space_floor_gb if settings else 0) * 1024**3)
    if free <= 0 or floor <= 0:
        return free
    return max(1, free - floor)


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
    new_hashes = _capture_new_hashes(qbt, hashes_before)
    return next(iter(new_hashes)) if len(new_hashes) == 1 else None


def _capture_new_hashes(qbt: QBTClient, hashes_before: set[str]) -> set[str]:
    """Every hash that appeared since `hashes_before`, once at least one
    has; raises QBTError when none ever does (see `_capture_new_hash`)."""
    for _ in range(config.HASH_CAPTURE_ATTEMPTS):
        new_hashes = qbt.existing_torrent_hashes() - hashes_before
        if new_hashes:
            return new_hashes
        time.sleep(config.HASH_CAPTURE_INTERVAL_SECONDS)
    raise QBTError("qBittorrent never actually added this torrent (still absent after checking several times)")


def same_release(torrent_name: str | None, file_name: str | None) -> bool:
    """Whether a torrent qBittorrent lists is the release a search row
    named — token sets, one within the other, so a tracker's "[Site]"
    prefix or a missing extension doesn't matter."""
    if not torrent_name or not file_name:
        return False
    extensions = {"mkv", "mp4", "avi", "torrent"}
    a, b = set(tokenize(torrent_name)) - extensions, set(tokenize(file_name)) - extensions
    return bool(a and b) and (a <= b or b <= a)


# A name only identifies a release when it still carries release
# metadata; "Title (Year)" after filing names every copy at once.
_RELEASE_METADATA_TOKENS = {"2160p", "1080p", "720p", "480p", "4k", "uhd", "remux", "bluray", "brrip", "bdrip", "web", "webrip", "webdl", "hdtv", "dvdrip", "x264", "x265", "h264", "h265", "hevc", "avc", "xvid"}


def _is_rejected_release(candidate: dict, rejected: dict) -> bool:
    """Whether a search row is a rejected copy: the same release name
    when that name still carries release metadata, or — only when a
    rejection had nothing but the file to go on — the same size to
    within a twentieth of a percent (10 MB on a 20 GB file: identical
    streams, not merely a similar encode; live 2026-09-17 a half-percent
    band swallowed a different 2160p release of Mutiny)."""
    size, rejected_size = candidate.get("fileSize") or 0, rejected.get("size_bytes") or 0
    if size > 0 and rejected_size > 0 and abs(size - rejected_size) <= rejected_size * 0.0005:
        return True
    name = rejected.get("name") or ""
    if set(tokenize(name)) & _RELEASE_METADATA_TOKENS:
        return same_release(candidate.get("fileName"), name)
    return False


def _claim_ours(qbt: QBTClient, new_hashes: set[str], winner: dict, stragglers: list[dict]) -> str | None:
    """Which of the hashes that appeared after an add is the one just
    added. A single new hash with nothing else in flight is ours. When an
    earlier candidate's add had timed out (`stragglers`) it may land late
    alongside ours — it's told apart by name and removed, so a request
    never ends up with two downloads. Ambiguous (a concurrent manual add,
    say) still resolves to None: added, but untracked."""
    if len(new_hashes) == 1 and not stragglers:
        return next(iter(new_hashes))
    ours: str | None = None
    for torrent_hash in sorted(new_hashes):
        try:
            info = qbt.torrent_info(torrent_hash)
        except QBTError:
            info = None
        name = (info or {}).get("name")
        if same_release(name, winner.get("fileName")):
            ours = torrent_hash
        elif any(same_release(name, s.get("fileName")) for s in stragglers):
            try:
                qbt.delete_torrent(torrent_hash, delete_files=True)
            except QBTError:
                pass
    if ours is None and len(new_hashes) == 1:
        return next(iter(new_hashes))
    return ours


@dataclass
class _AddAttempt:
    """One candidate's add outcome — either a genuine success (`succeeded`,
    `torrent_hash` set) or a failure worth remembering as the audit trail
    if every candidate for this request ultimately fails (see
    `last_failed_attempt` in both `download()` and `download_episode()`)."""

    winner: dict
    score: Score
    succeeded: bool = False
    torrent_hash: str | None = None
    error: str | None = None


def _rank_and_add(
    qbt: QBTClient, candidates: list[dict], free_space_bytes: int, category: str, existing_hashes: set[str]
) -> tuple[list[tuple[dict, Score]], _AddAttempt | None]:
    """Shared by `download()` (movies) and `download_episode()` (Stage 10,
    TV) — ranking, the free-space fit filter, and the add-with-hash-capture
    retry loop don't depend on what kind of media is being downloaded, only
    the search/identity/relevance side above this does. Tries each fitting
    candidate (best-ranked first) until one actually lands in qBittorrent,
    falling through to the next on a confirmed add failure (a real-world
    case: a scraper listing a link qBittorrent can never fetch) rather than
    failing the whole request while other candidates remain untried.
    Returns the fitting list (so a caller can tell "nothing fit" from
    "nothing to rank" apart) plus either the successful attempt or the
    *last* failed one, for a full audit trail either way — "hidden, never
    unrecoverable"."""
    ranked = rank_candidates(candidates)
    fitting = _candidates_that_fit(ranked, free_space_bytes)

    last_attempt: _AddAttempt | None = None
    stragglers: list[dict] = []  # candidates whose add never showed up in time — may still land late
    for winner, winner_score in fitting:
        # One add at a time, process-wide. Searching runs concurrently
        # now (config.SEARCH_CONCURRENCY), but this block identifies the
        # torrent it just added by diffing qBittorrent's hash list around
        # the add — so two overlapping adds would each see the other's
        # torrent appear and could claim it. Held per attempt rather than
        # for the whole loop, so a candidate that never materialises
        # doesn't keep every other request's add waiting behind it.
        with _ADD_LOCK:
            qbt.ensure_category(category)
            # Re-read inside the lock: the caller's snapshot was taken
            # before a search that may have run for minutes alongside
            # other requests' adds, and a baseline that stale would make
            # their torrents look like this one's.
            baseline = qbt.existing_torrent_hashes() | existing_hashes
            try:
                qbt.add_torrent(winner["fileUrl"], category=category)
                new_hashes = _capture_new_hashes(qbt, baseline)
            except QBTError as exc:
                last_attempt = _AddAttempt(winner=winner, score=winner_score, error=str(exc))
                stragglers.append(winner)
                continue
            torrent_hash = _claim_ours(qbt, new_hashes, winner, stragglers)
        return fitting, _AddAttempt(winner=winner, score=winner_score, succeeded=True, torrent_hash=torrent_hash)

    return fitting, last_attempt


def download(
    tmdb_id: int,
    tmdb_client: TMDBClient,
    qbt: QBTClient,
    settings: PipelineSettings | None = None,
    excluded_hashes: set[str] | None = None,
    excluded_releases: list[dict] | None = None,
) -> DownloadResult:
    """`excluded_hashes` (Stage 15): torrent hashes explicitly rejected as
    genuinely defective on a previous attempt for this same movie (see
    `db.RequestStore.add_rejected_torrent`) — merged in alongside whatever's
    already in qBittorrent, so a fresh search never re-selects the exact
    same bad release, and instead falls through to the next-best-scored
    candidate. `exclude_existing` (reused by every search helper below)
    already treats "already in qBittorrent" and "explicitly rejected" as
    the identical kind of exclusion, so nothing else has to change."""
    settings = settings or PipelineSettings.from_config()
    identity = resolve(tmdb_id, tmdb_client)
    existing_hashes = qbt.existing_torrent_hashes() | (excluded_hashes or set())
    free_space_bytes = usable_free_space(qbt, settings)

    candidates = _merge_variant_candidates(
        identity.variants,
        lambda variant: _search_variant(qbt, variant, identity, existing_hashes, settings, excluded_releases),
    )
    if not candidates:
        return DownloadResult(status="no qualifying results", identity=identity)

    fitting, attempt = _rank_and_add(qbt, candidates, free_space_bytes, settings.category, existing_hashes)
    if not fitting or attempt is None:
        return DownloadResult(status="insufficient free space", identity=identity)

    variant_used = attempt.winner.get("_variant_used")
    if attempt.succeeded:
        return DownloadResult(
            status="added",
            identity=identity,
            variant_used=variant_used,
            winner=attempt.winner,
            score=attempt.score,
            candidates_considered=len(candidates),
            torrent_hash=attempt.torrent_hash,
        )

    return DownloadResult(
        status="add failed",
        identity=identity,
        variant_used=variant_used,
        winner=attempt.winner,
        score=attempt.score,
        candidates_considered=len(candidates),
        error=attempt.error,
    )


def _search_episode_variant(
    qbt: QBTClient,
    variant: str,
    identity: ShowIdentity,
    season: int,
    episode: int,
    existing_hashes: set[str],
    settings: PipelineSettings,
) -> list[dict]:
    query = episode_query(variant, season, episode)
    # See `_search_variant`'s comment above — searching "all" rather than
    # `config.TV_CATEGORY` for the same reason (a real plugin returning zero
    # results when category-filtered). `config.TV_CATEGORY` is still what
    # the torrent gets labeled as once added, in `_rank_and_add` below.
    raw_results = qbt.search(query, category="all")
    trustworthy = [r for r in raw_results if is_trustworthy(r)]
    relevant = [
        r
        for r in trustworthy
        if passes_episode_relevance_gate(r.get("fileName", ""), identity, season, episode, settings)
    ]
    viable = [r for r in relevant if passes_viability_gate(r, settings)]
    deduped = dedup_candidates(viable)
    return exclude_existing(deduped, existing_hashes)


def find_best_episode_candidate(
    identity: ShowIdentity,
    season: int,
    episode: int,
    qbt: QBTClient,
    settings: PipelineSettings | None = None,
    excluded_hashes: set[str] | None = None,
) -> tuple[dict, Score] | None:
    """Read-only peek at what `download_episode()` would add right now,
    without actually adding it — Stage 12.x's auto-recheck loop uses this
    to decide *whether* a fresh search has turned up something worth
    switching to, before ever touching qBittorrent's `add_torrent`. Same
    fallback-across-variants and free-space-fit filtering as the real add
    path (`_rank_and_add`), so "would this get added" and "did this get
    added" never quietly disagree. `None` if nothing fitting turns up
    across every variant.

    `excluded_hashes` (Stage 15): same rejected-torrent exclusion as
    `download()` — see its own docstring."""
    settings = settings or PipelineSettings.from_config()
    existing_hashes = qbt.existing_torrent_hashes() | (excluded_hashes or set())
    free_space_bytes = usable_free_space(qbt, settings)

    candidates = _merge_variant_candidates(
        identity.variants,
        lambda variant: _search_episode_variant(qbt, variant, identity, season, episode, existing_hashes, settings),
    )
    if not candidates:
        return None
    ranked = rank_candidates(candidates)
    fitting = _candidates_that_fit(ranked, free_space_bytes)
    return fitting[0] if fitting else None


def download_episode(
    identity: ShowIdentity,
    season: int,
    episode: int,
    qbt: QBTClient,
    settings: PipelineSettings | None = None,
    excluded_hashes: set[str] | None = None,
) -> EpisodeDownloadResult:
    """The Stage 10 equivalent of `download()`, for one specific episode.
    Takes an already-resolved `ShowIdentity` rather than a tmdb_id + TMDB
    client — unlike a movie request, an episode request is always issued
    against a show that's already been resolved (Stage 9's `resolve_show`,
    or Stage 12's subscription row), so there's no TMDB call to make here.
    Reuses `_rank_and_add` unchanged; only the search/relevance side above
    it (episode identity instead of title+year, `config.TV_CATEGORY`
    instead of `settings.category`) differs from `download()`.

    `excluded_hashes` (Stage 15): same rejected-torrent exclusion as
    `download()` — see its own docstring."""
    settings = settings or PipelineSettings.from_config()
    existing_hashes = qbt.existing_torrent_hashes() | (excluded_hashes or set())
    free_space_bytes = usable_free_space(qbt, settings)

    candidates = _merge_variant_candidates(
        identity.variants,
        lambda variant: _search_episode_variant(qbt, variant, identity, season, episode, existing_hashes, settings),
    )
    if not candidates:
        return EpisodeDownloadResult(status="no qualifying results", identity=identity, season=season, episode=episode)

    fitting, attempt = _rank_and_add(qbt, candidates, free_space_bytes, config.TV_CATEGORY, existing_hashes)
    if not fitting or attempt is None:
        return EpisodeDownloadResult(
            status="insufficient free space", identity=identity, season=season, episode=episode
        )

    variant_used = attempt.winner.get("_variant_used")
    if attempt.succeeded:
        return EpisodeDownloadResult(
            status="added",
            identity=identity,
            season=season,
            episode=episode,
            variant_used=variant_used,
            winner=attempt.winner,
            score=attempt.score,
            candidates_considered=len(candidates),
            torrent_hash=attempt.torrent_hash,
        )

    return EpisodeDownloadResult(
        status="add failed",
        identity=identity,
        season=season,
        episode=episode,
        variant_used=variant_used,
        winner=attempt.winner,
        score=attempt.score,
        candidates_considered=len(candidates),
        error=attempt.error,
    )


def _search_pack_queries(
    qbt: QBTClient,
    queries: list[str],
    gate,
    existing_hashes: set[str],
    settings: PipelineSettings,
) -> list[dict]:
    """Searches every query string for one variant — not stopping at the
    first with results — and returns the combined, deduped pool, each
    candidate tagged with the literal query that found it (`_query_used`,
    read back off the eventual winner). A season pack gets two real-world
    query shapes tried per variant (`tv_resolve.season_pack_queries`); a
    series pack just the one (`series_pack_query`). Same "combine
    everything, rank across the whole pool" reasoning as
    `_merge_variant_candidates` above, one level lower (query shape within
    one variant, rather than variant itself) — same trust/gate/viability
    pipeline as every other search helper here, parameterized on which
    pack gate (`gate`) applies."""
    combined: list[dict] = []
    for query in queries:
        raw_results = qbt.search(query, category="all")
        trustworthy = [r for r in raw_results if is_trustworthy(r)]
        relevant = [r for r in trustworthy if gate(r.get("fileName", ""), settings)]
        viable = [r for r in relevant if passes_viability_gate(r, settings)]
        deduped = dedup_candidates(viable)
        candidates = exclude_existing(deduped, existing_hashes)
        for candidate in candidates:
            tagged = dict(candidate)
            tagged["_query_used"] = query
            combined.append(tagged)
    return dedup_candidates(combined)


def _best_season_pack_tier(qbt: QBTClient, identity: ShowIdentity, existing_hashes: set[str], settings: PipelineSettings) -> int:
    """The best resolution tier a season pack offers, probing season 1
    with the show's main title only — enough to tell whether the seasons
    are better than a whole-series pack, without a full second search."""

    def gate(file_name: str, s: PipelineSettings) -> bool:
        return passes_season_pack_gate(file_name, identity, 1, s)

    found = _search_pack_queries(qbt, season_pack_queries(identity.variants[0], 1), gate, existing_hashes, settings)
    if not found:
        return 0
    return rank_candidates(found)[0][1].resolution_score


def _tier_label(tier: int) -> str:
    for score, phrases in config.RESOLUTION_TIERS:
        if score == tier:
            return phrases[0]
    return "unknown quality"


def download_pack(
    identity: ShowIdentity,
    scope: str,
    qbt: QBTClient,
    settings: PipelineSettings | None = None,
    season: int | None = None,
    season_range_end: int | None = None,
    excluded_hashes: set[str] | None = None,
) -> PackDownloadResult:
    """Stage 13 (+ Stage 14.x's season-range scope): search/score/add for a
    whole-season, multi-season-range, or complete-series pack. Reuses
    `_rank_and_add` unchanged, same as `download_episode`; only the
    search/relevance side (a pack-shaped gate instead of an episode-
    identity one, pack-shaped query strings instead of an episode query)
    differs.

    `scope` is `"season"` (requires `season`), `"season_range"` (requires
    both `season` as the range's start and `season_range_end` as its
    inclusive end), or `"series"`. Takes an already-resolved `ShowIdentity`
    rather than a tmdb_id + TMDB client, same reasoning as
    `download_episode`: a bulk-download request is always issued against a
    show the caller has already resolved.

    `excluded_hashes` (Stage 15): same rejected-torrent exclusion as
    `download()` — see its own docstring. Added as the last parameter
    (rather than alongside `settings`) so existing positional callers
    (worker.py's `download_pack(identity, scope, qbt, settings, season,
    season_range_end)`) keep working unchanged."""
    if scope not in ("season", "season_range", "series"):
        raise ValueError(f"scope must be 'season', 'season_range', or 'series', got {scope!r}")
    if scope == "season" and season is None:
        raise ValueError("season is required when scope == 'season'")
    if scope == "season_range" and (season is None or season_range_end is None):
        raise ValueError("season and season_range_end are both required when scope == 'season_range'")

    settings = settings or PipelineSettings.from_config()
    existing_hashes = qbt.existing_torrent_hashes() | (excluded_hashes or set())
    free_space_bytes = usable_free_space(qbt, settings)

    combined: list[dict] = []
    for variant in identity.variants:
        if scope == "season":
            queries = season_pack_queries(variant, season)

            def gate(file_name: str, s: PipelineSettings, _season=season) -> bool:
                return passes_season_pack_gate(file_name, identity, _season, s)
        elif scope == "season_range":
            queries = season_range_pack_queries(variant, season, season_range_end)

            def gate(file_name: str, s: PipelineSettings, _start=season, _end=season_range_end) -> bool:
                return passes_season_range_pack_gate(file_name, identity, _start, _end, s)
        else:
            queries = series_pack_queries(variant)

            def gate(file_name: str, s: PipelineSettings) -> bool:
                return passes_series_pack_gate(file_name, identity, s)

        for candidate in _search_pack_queries(qbt, queries, gate, existing_hashes, settings):
            tagged = dict(candidate)
            tagged.setdefault("_variant_used", variant)
            combined.append(tagged)

    candidates = dedup_candidates(combined)
    if not candidates:
        return PackDownloadResult(
            status="no qualifying results", identity=identity, scope=scope, season=season,
            season_range_end=season_range_end,
        )

    if scope == "series":
        # A whole-series pack is convenient, not sacred. Confirmed live
        # 2026-09-17 (Ted): the only series pack was S01-S02 at 720p while
        # every season had 2160p and 1080p packs of its own. When the
        # seasons come in better, skip the series pack and let the
        # worker's fallback ask for each season instead.
        best_result, best_score = rank_candidates(candidates)[0]
        best_series = best_score.resolution_score
        best_season = _best_season_pack_tier(qbt, identity, existing_hashes, settings)
        # Only on stated evidence. A pack naming no resolution sits at the
        # SD tier because its source implies at least that much — an
        # understatement on purpose (config.RESOLUTION_TIERS) — and
        # reading it as the pack's real quality abandons the thing that
        # was asked for in favour of a 720p season pack. Found on
        # "Justice League Unlimited 2001 S01-05 Bluray x265": 69 seeders,
        # the healthiest pack on offer, at tier 1 for saying "Bluray"
        # where the season packs say "720p".
        stated = states_resolution(tokenize(best_result.get("fileName", "")))
        if best_season > best_series and stated:
            return PackDownloadResult(
                status="no qualifying results",
                identity=identity,
                scope=scope,
                candidates_considered=len(candidates),
                note=f"The only whole-series pack is {_tier_label(best_series)}; the seasons come in {_tier_label(best_season)}",
            )

    fitting, attempt = _rank_and_add(qbt, candidates, free_space_bytes, config.TV_CATEGORY, existing_hashes)
    if not fitting or attempt is None:
        return PackDownloadResult(
            status="insufficient free space", identity=identity, scope=scope, season=season,
            season_range_end=season_range_end,
        )

    variant_used = attempt.winner.get("_variant_used")
    query_used = attempt.winner.get("_query_used")
    if attempt.succeeded:
        return PackDownloadResult(
            status="added",
            identity=identity,
            scope=scope,
            season=season,
            season_range_end=season_range_end,
            variant_used=variant_used,
            query_used=query_used,
            winner=attempt.winner,
            score=attempt.score,
            candidates_considered=len(candidates),
            torrent_hash=attempt.torrent_hash,
        )

    return PackDownloadResult(
        status="add failed",
        identity=identity,
        scope=scope,
        season=season,
        season_range_end=season_range_end,
        variant_used=variant_used,
        query_used=query_used,
        winner=attempt.winner,
        score=attempt.score,
        candidates_considered=len(candidates),
        error=attempt.error,
    )
