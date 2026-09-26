"""Two-pass candidate filtering: pass one (relevance gate) decides whether a
search result is actually the title Stage 1 resolved; pass two (quality
score) ranks everything that survives pass one. Same shared token utility
(`app.normalize`) as Stage 1's title-variant matching."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app import config
from app.normalize import extract_episode_identity, normalize_text, tokenize
from app.pipeline_settings import PipelineSettings
from app.resolve import MediaIdentity

_YEAR_TOKEN_RE = re.compile(r"^(19|20)\d{2}$")
_INFOHASH_RE = re.compile(r"btih:([a-zA-Z0-9]+)")
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
_SOLO_SEASON_RE = re.compile(r"^s\d{1,2}$")


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

    # "descrLink-only": some plugins (confirmed live — limetorrents, Stage
    # 12's real Lanterns S01E03 validation) hand back the site's own details
    # *webpage* as `fileUrl`, identical to `descrLink`, instead of a real
    # magnet/.torrent link. qBittorrent's add doesn't raise on this — it
    # just fetches the HTML, fails to parse it as a torrent, and the
    # torrent silently never gets indexed, which looked from the outside
    # like every candidate "failing to add" for no visible reason. This was
    # always a named Stage 2 deliverable ("skip descrLink-only results")
    # but was never actually implemented until this was caught live.
    descr_link = (result.get("descrLink") or "").strip()
    if descr_link and file_url == descr_link:
        return False

    host = urlparse(file_url).hostname
    return host is not None and host.lower() not in _LOCAL_HOSTS


# ---------------------------------------------------------------------------
# Pass one: relevance gate
# ---------------------------------------------------------------------------


def contains_phrase(tokens: list[str], phrase: str) -> bool:
    """Public (Stage 13: reused by pack_score.py for "season N"/"complete
    series" phrase matching — the same whole-token, order-preserving
    substring-of-tokens check title-variant matching already relies on)."""
    phrase_tokens = tokenize(phrase)
    if not phrase_tokens:
        return False
    n = len(phrase_tokens)
    return any(tokens[i : i + n] == phrase_tokens for i in range(len(tokens) - n + 1))


# What may follow a title in a release name for the match to count: the
# release metadata a scene name puts straight after the title (season
# and episode markers, a year, resolution, source, codec, edition, region
# and language tags, streaming-service tags). Anything else between the
# title and this metadata is more title — the name belongs to a
# different, longer title ("Ted Lasso" for "Ted", "Batman Beyond" for
# "Batman").
_TITLE_BOUNDARY_WORDS = (
    "season seasons episode complete series miniseries tv show "
    "4k uhd sd hd fhd hdr hdr10 dv dovi sdr "
    "web webrip webdl dl bluray bdrip brrip remux hdtv pdtv dvdrip dvd dvdr hdrip tvrip cam ts tc "
    "x264 x265 h264 h265 hevc avc xvid divx aac ac3 dts ddp atmos truehd "
    "unrated extended theatrical directors remastered proper repack rerip internal limited imax hybrid multi dual dubbed subbed uncut "
    "us uk au ca nz eng english ita italian french german spanish latino hindi korean japanese "
    "amzn nf dsnp atvp pcok hmax hulu max "
    "mkv mp4 avi"
).split()
_TITLE_BOUNDARY_RE = re.compile(
    r"^(s\d{1,2}(e\d{1,3})?|e\d{1,3}|(19|20)\d{2}|\d{3,4}p|\d{1,2}bit|" + "|".join(_TITLE_BOUNDARY_WORDS) + r")$"
)


# Two-word metadata that starts with an ordinary word ("The Complete
# Series", "All Seasons", "Full Series").
_TITLE_BOUNDARY_PAIRS = {("the", "complete"), ("the", "movie"), ("all", "seasons"), ("full", "series"), ("entire", "series"), ("the", "series")}


# Junk a release name may start with before the title: tracker and
# group tags, a site name. Anything else in front of the title is more
# title — the name belongs to a different show ("The Batman", "Better
# Off Ted").
_LEADING_NOISE = {"www", "com", "net", "org", "torrent", "torrents", "p", "rarbg", "yts", "eztv", "ettv", "tgx"}


def _title_stands_alone(
    tokens: list[str], start: int, end: int, year: int | None, year_tolerance: int | None = None
) -> bool:
    """The title at tokens[start:end] is the release's own title: nothing
    but noise or metadata before it, metadata after it, and any year
    sitting right after it is this title's year (`year`, when known).

    `year_tolerance` overrides config.YEAR_TOLERANCE for callers whose
    year signal is weaker than a single title's — see
    config.PACK_YEAR_TOLERANCE, and pack_score.py, which is the only one
    that passes it."""
    if start > 0:
        before = tokens[start - 1]
        if not (
            _TITLE_BOUNDARY_RE.match(before)
            or before in _LEADING_NOISE
            or (start > 1 and (tokens[start - 2], before) in _TITLE_BOUNDARY_PAIRS)
        ):
            return False
    if not _metadata_follows(tokens, end):
        return False
    tolerance = config.YEAR_TOLERANCE if year_tolerance is None else year_tolerance
    if year and end < len(tokens) and _YEAR_TOKEN_RE.match(tokens[end]) and abs(int(tokens[end]) - year) > tolerance:
        return False
    return True


def _metadata_follows(tokens: list[str], after: int) -> bool:
    if after >= len(tokens):
        return True
    if _TITLE_BOUNDARY_RE.match(tokens[after]):
        return True
    return after + 1 < len(tokens) and (tokens[after], tokens[after + 1]) in _TITLE_BOUNDARY_PAIRS


def _phrase_positions(tokens: list[str], phrase_tokens: list[str]) -> list[int]:
    n = len(phrase_tokens)
    return [i for i in range(len(tokens) - n + 1) if tokens[i : i + n] == phrase_tokens]


def matches_any_variant(
    tokens: list[str], variants: list[str], year: int | None = None, year_tolerance: int | None = None
) -> bool:
    """Public (Stage 10: reused by tv_score.py's episode gate — a show's
    title-variant list is matched the exact same way a movie's is, so this
    takes the plain variant list rather than a MediaIdentity, decoupling it
    from any one identity type).

    A variant matches when it is the release's own title: nothing but
    tracker noise or metadata in front of it, release metadata straight
    after it ("Ted.S01E01", "Batman.S01E01", "Lioness.S01E01"), and any
    year right after it within a year of `year` when that's known. More
    title words on either side mean another show: "Ted.Lasso", "Batman.
    Beyond", "The.Batman.2004", "Better.Off.Ted.2009" — every one of
    those was fetched live for Ted or Batman: The Brave and the Bold
    (2026-09-16/17) before these checks existed."""
    if not variants:
        return False
    for variant in variants:
        phrase_tokens = tokenize(variant)
        if not phrase_tokens:
            continue
        positions = _phrase_positions(tokens, phrase_tokens)
        if not positions:
            continue
        for start in positions:
            if _title_stands_alone(tokens, start, start + len(phrase_tokens), year, year_tolerance):
                return True
    return False


def _year_within_tolerance(tokens: list[str], release_year: int | None) -> bool:
    if release_year is None:
        return True
    found_years = [int(t) for t in tokens if _YEAR_TOKEN_RE.match(t)]
    if not found_years:
        return True  # no year token present in the filename — nothing to contradict
    return any(abs(year - release_year) <= config.YEAR_TOLERANCE for year in found_years)


def _resolution_score(tokens: list[str]) -> int:
    return _best_tier(tokens, config.RESOLUTION_TIERS)


def states_resolution(tokens: list[str]) -> bool:
    """True when the name names a resolution, rather than one being
    inferred from its source.

    RESOLUTION_TIERS deliberately conflates the two so a floor accepts a
    DVDRip or a Blu-ray that never says a number. That is right for a
    floor and wrong for a comparison: the SD tier such a release lands on
    is a deliberate understatement, and reading it as the release's
    actual quality makes an untagged Blu-ray look worse than a 720p file.
    Callers weighing two releases against each other ask this first."""
    for _tier, phrases in config.RESOLUTION_TIERS:
        for phrase in phrases:
            if phrase in config.INFERRED_SD_SOURCES:
                continue
            if contains_phrase(tokens, phrase):
                return True
    return False


def _resolution_floor_tier(min_resolution: str) -> int:
    """The tier value for a `min_resolution` setting. An unrecognized
    setting fails safe to the strictest (highest) known tier rather than
    silently admitting everything."""
    target = normalize_text(min_resolution)
    for tier, phrases in config.RESOLUTION_TIERS:
        if any(normalize_text(phrase) == target for phrase in phrases):
            return tier
    return max(tier for tier, _ in config.RESOLUTION_TIERS)


def passes_resolution_floor(tokens: list[str], min_resolution: str) -> bool:
    """A candidate must carry a *recognized* resolution token at or above
    the configured floor — unrecognized/absent resolution info fails safe
    rather than being guessed at. Any tier above the floor is still fine:
    this is a floor, not a fixed target, so a 2160p release passes a
    "1080p" floor just as a 1080p one does. Public: Stage 10 reuses this
    unchanged for episode matching, same floor/setting either way."""
    return _resolution_score(tokens) >= _resolution_floor_tier(min_resolution)


def passes_language_filter(
    tokens: list[str],
    allowlist: tuple[str, ...],
    blocklist: tuple[str, ...],
    required: tuple[str, ...] = (),
) -> bool:
    """`allowlist` is OR semantics (at least one qualifies); `required` is
    AND semantics (every language listed must be present — e.g. a dual-
    audio release needs English *and* French together, not just one),
    an independent, stricter requirement layered on top of it. Public:
    reused unchanged by tv_score.py (Stage 10) and pack_score.py
    (Stage 13) — a release's language tags mean the same thing whether
    it's a movie, an episode, or a pack."""
    if any(normalize_text(blocked) in tokens for blocked in blocklist):
        return False
    if required and not all(normalize_text(req) in tokens for req in required):
        return False
    if allowlist:
        return any(normalize_text(allowed) in tokens for allowed in allowlist)
    return True


def passes_cam_filter(tokens: list[str]) -> bool:
    """Rejects releases explicitly tagged as a cam/telesync/screener rip.
    Doesn't (can't) catch a bootleg that just omits any source tag — see
    config.py's CAM_BLOCKLIST comment. Public: reused unchanged by
    tv_score.py (Stage 10)."""
    return not any(normalize_text(blocked) in tokens for blocked in config.CAM_BLOCKLIST)


def passes_non_video_filter(tokens: list[str]) -> bool:
    """Rejects a result whose name carries an archive or executable
    marker (.zip, .zipx, .rar, .7z, .exe, ...) instead of a raw video
    file — same whole-token blocklist check as `passes_cam_filter`, just
    against config.NON_VIDEO_BLOCKLIST. Doesn't (can't) catch a disguised
    payload that omits any such marker from its name; see config.py's
    NON_VIDEO_BLOCKLIST comment for the real-world cases (an unfileable
    .zipx, then a fake-release .exe) this closes. Public: reused
    unchanged by tv_score.py and pack_score.py."""
    return not any(normalize_text(blocked) in tokens for blocked in config.NON_VIDEO_BLOCKLIST)


def passes_not_a_tv_episode_filter(tokens: list[str]) -> bool:
    """Rejects a movie candidate whose filename is actually shaped like TV
    content — a whole "s01e04"-style episode token (contiguous or split
    across two adjacent tokens), or a standalone "s01"-style season token
    (a season/complete-series pack) — same title, wrong kind of thing.
    Confirmed live: a movie search for "Mayday" (2026) turned up an
    episode of the unrelated long-running documentary series of the same
    name (2026-09-14) — `matches_any_variant` alone can't tell those
    apart, since the release name carries the exact title as a whole
    token same as the real movie would. A genuine movie release never
    carries either shape, so their presence here is a dead giveaway of
    exactly this cross-media title collision — distinct from
    `passes_non_video_filter`'s job of catching an archive/executable
    marker."""
    if extract_episode_identity(tokens) is not None:
        return False
    return not any(_SOLO_SEASON_RE.match(token) for token in tokens)


def passes_relevance_gate(file_name: str, identity: MediaIdentity, settings: PipelineSettings | None = None) -> bool:
    settings = settings or PipelineSettings.from_config()
    tokens = tokenize(file_name)
    return (
        matches_any_variant(tokens, identity.variants, identity.release_year)
        and _year_within_tolerance(tokens, identity.release_year)
        and passes_resolution_floor(tokens, settings.min_resolution)
        and passes_language_filter(
            tokens, settings.language_allowlist, settings.language_blocklist, settings.language_required
        )
        and passes_cam_filter(tokens)
        and passes_non_video_filter(tokens)
        and passes_not_a_tv_episode_filter(tokens)
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

# Priority: resolution > swarm health > source > codec > container.
#
# Resolution is absolute — swarm health never buys a lower resolution,
# because a 1080p copy is not the thing that was asked for however many
# people are seeding it. Everything below resolution answers a different
# question: of the copies at the resolution you asked for, which one
# actually arrives? That is the healthiest swarm, not the best-labelled
# release, and three separate live cases say so:
#
#   - Dune: Part Two, 2026-09-04 — a 267-seeder REMUX lost to a
#     10-seeder release of the same source/codec tier purely because
#     the loser's name said "MP4" and the winner's container was
#     unstated (near-certainly MKV by REMUX convention; TrueHD/Atmos
#     barely fits in MP4 anyway).
#   - Mutiny, 2026-09-17 — a near-dead 2160p REMUX kept beating
#     well-seeded copies of the same resolution.
#   - The Empty Man, 2026-09-21 — a 2160p pick on a handful of seeders
#     beat one on far more, because both sat in the same coarse seeder
#     tier and codec/container decided it.
#
# Source, codec and container are weak, noisy signals read out of a
# filename a stranger wrote; seeder count is measured. So they rank
# below it and only decide between candidates of comparable health —
# which, with SEEDER_TIERS' finer ladder, means genuinely comparable
# rather than "both somewhere above 100".
#
# Rescaled 2026-09-21 for that finer ladder: seven tiers at
# the old weight of 1000 would have reached 7000 and started colliding
# with resolution's 10000 step, i.e. swarm health silently buying a lower
# resolution — the one thing it must never do. Each weight still exceeds
# the largest possible sum of everything below it:
#   container  2·1                      = 2
#   codec      2·10                     = 20   > 2
#   source     5·100                    = 500  > 22
#   seeders    7·10000                  = 70000 > 522
#   resolution 4·100000                 = ...  > 70522
_RESOLUTION_WEIGHT = 100000
_SEEDER_WEIGHT = 10000
_SOURCE_WEIGHT = 100
_CODEC_WEIGHT = 10
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
        # Proven-alive outranks everything, including resolution.
        #
        # Anything that reaches ranking has either cleared MIN_SEEDERS or
        # reported no seeder count at all, because passes_viability_gate
        # deliberately lets "unknown" (-1) through — "unknown != zero",
        # Stage 0. So this flag is precisely "we know this swarm exists"
        # versus "we are guessing", and guessing now loses to knowing.
        #
        # It used to sit *behind* `composite`, which made it nearly
        # inert: resolution is weighted 10000 and a whole tier of swarm
        # health only 1000, so an unreported swarm at a better resolution
        # beat every verified one. Live 2026-09-20, The Empty Man: a
        # 2160p WEB-DL whose plugin reported nothing won over a 2160p
        # with 62 seeders and a 1080p with 124, landed with zero seeds,
        # and sat at 0% for two days.
        #
        # Quality ordering is untouched *among* candidates we can verify
        # — a 2160p with 62 seeders still beats a 1080p with 124, which
        # is the point. An unreported swarm just stops being treated as
        # a peer of a measured one and becomes the last resort it always
        # was, still eligible when nothing verifiable turns up.
        #
        # Size stays last: two releases can differ by <0.1% for reasons
        # as trivial as a bundled sample file, which shouldn't outweigh
        # anything above it.
        seeders_known = 1 if self.seeders >= 0 else 0
        return (seeders_known, self.composite, self.seeders, self.size_bytes)


def _best_tier(tokens: list[str], tiers: tuple) -> int:
    for score, phrases in tiers:
        if any(contains_phrase(tokens, phrase) for phrase in phrases):
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
