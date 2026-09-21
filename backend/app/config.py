import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from app.db import RequestStore

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

# Tracked separately from the defaulted QBIT_* constants below so the
# frontend migration's setup wizard/Connections panel (Part E) can tell
# "genuinely set via env" apart from "just using the hardcoded fallback" —
# QBIT_HOST is never actually None/empty the way TMDB_API_KEY can be, so
# truthiness alone can't answer that question the way it can for TMDB.
_QBIT_HOST_ENV = os.environ.get("QBIT_HOST")
_QBIT_PORT_ENV = os.environ.get("QBIT_PORT")
_QBIT_USERNAME_ENV = os.environ.get("QBIT_USERNAME")
_QBIT_PASSWORD_ENV = os.environ.get("QBIT_PASSWORD")

QBIT_HOST = _QBIT_HOST_ENV or "192.168.0.133"
QBIT_PORT = int(_QBIT_PORT_ENV or "30024")
QBIT_USERNAME = _QBIT_USERNAME_ENV or ""
QBIT_PASSWORD = _QBIT_PASSWORD_ENV or ""

# -- Frontend migration Part E: first-run setup wizard, DB-backed with an
#    env-var override. An env var always wins when set, so an existing
#    .env-based install (including this app's own NAS deploy) needs zero
#    changes; otherwise the value comes from the settings table, editable
#    through the wizard/Settings' Connections panel — the inverse of
#    pipeline_settings.py's convention (there the DB is primary, config.py
#    only a fallback default), because these are per-installation secrets
#    an env-var user has already handled via infra-as-code, not tunable
#    behavior every install is expected to configure through the UI.
#
#    Known, accepted gap: both api.py's lifespan (the worker's own
#    TMDBClient/QBTClient) and its get_tmdb/get_qbt route dependencies
#    resolve these only once, at startup — not per-request — so a value
#    saved through the wizard needs a restart to actually take effect.
#    (A per-request re-resolve was tried and reverted: it broke the test
#    suite's fake-injection pattern for app.state.tmdb/app.state.qbt —
#    see get_tmdb's own docstring in api.py.) Narrow in practice: setup
#    happens once, at first boot, before anything's depended on either
#    value yet — the wizard's own final step can just say "restart to
#    apply" rather than actually needing to solve this. --


def resolve_tmdb_api_key(store: "RequestStore") -> str | None:
    return TMDB_API_KEY or store.get_settings().get("tmdb_api_key")


def tmdb_api_key_source(store: "RequestStore") -> str | None:
    """"env" | "db" | None — which of the two resolve_tmdb_api_key() above
    actually used. Drives /api/setup/status and PUT /api/setup/tmdb's
    "can't override an env var from the UI" refusal."""
    if TMDB_API_KEY:
        return "env"
    return "db" if store.get_settings().get("tmdb_api_key") else None


@dataclass(frozen=True)
class QbtConnection:
    host: str
    port: int
    username: str
    password: str


def resolve_qbt_config(store: "RequestStore") -> QbtConnection:
    settings = store.get_settings()
    return QbtConnection(
        host=QBIT_HOST if _QBIT_HOST_ENV else (settings.get("qbt_host") or QBIT_HOST),
        port=QBIT_PORT if _QBIT_PORT_ENV else int(settings.get("qbt_port") or QBIT_PORT),
        username=QBIT_USERNAME if _QBIT_USERNAME_ENV else (settings.get("qbt_username") or QBIT_USERNAME),
        password=QBIT_PASSWORD if _QBIT_PASSWORD_ENV else (settings.get("qbt_password") or QBIT_PASSWORD),
    )


def qbt_config_source(store: "RequestStore") -> str:
    """"env" | "db" — qBittorrent connection fields are set as one group
    through the wizard/Connections panel, not field-by-field, so one
    combined source (env wins if *any* of the four vars is set) is enough,
    unlike TMDB's single-value source above."""
    if _QBIT_HOST_ENV or _QBIT_PORT_ENV or _QBIT_USERNAME_ENV or _QBIT_PASSWORD_ENV:
        return "env"
    return "db"

# -- Stage 3: API/persistence layer. --

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).resolve().parent.parent / "data" / "app.db"))

# How often the download watcher polls qBittorrent for requests already
# added and sitting in "downloading", to flip them to "complete". Doesn't
# hold up the pipeline lock — see worker.py.
DOWNLOAD_POLL_INTERVAL_SECONDS = int(os.environ.get("DOWNLOAD_POLL_INTERVAL_SECONDS", "10"))

# After add_torrent(), how many times (and how far apart) pipeline.py
# retries diffing existing_torrent_hashes() to find the new torrent's hash.
# A direct-.torrent-URL result isn't indexed by qBittorrent instantly —
# confirmed live during Stage 3 validation (torlock winner, Big Buck Bunny),
# and a slow tracker can take well over five seconds to hand the file
# over (live 2026-09-17: a Ted season pack landed after the pipeline had
# given up on it and added the next candidate, so both downloaded). A
# generous window costs a slow request some seconds; a short one costs
# a duplicate download.
HASH_CAPTURE_ATTEMPTS = 40
HASH_CAPTURE_INTERVAL_SECONDS = 1.0

# How many requests may be searched at once.
#
# Searching used to be strictly serial: one asyncio.Lock wrapped a whole
# request, and a request is not one search — it runs one search per
# title variant, each polling qBittorrent for up to
# SEARCH_CEILING_SECONDS. Queue a handful of things and the last one
# waits minutes in "queued" while an earlier one, already added, sits at
# the top of the requests list marked "downloading" — which reads as the
# download blocking the queue, though it isn't: _run_one returns the
# moment a torrent is added and never waits for the transfer.
#
# What the lock genuinely protected is the *add*, which identifies "the
# torrent I just added" by diffing qBittorrent's hash list before and
# after; two concurrent adds would each see the other's torrent appear.
# That section is still exclusive (pipeline._ADD_LOCK), so only the
# searching runs in parallel. 3 keeps well clear of qBittorrent's own
# concurrent-search-job limit while a request may itself fire several
# searches in sequence.
SEARCH_CONCURRENCY = int(os.environ.get("SEARCH_CONCURRENCY", "3"))

# How long one qBittorrent search job may run before its results are
# taken as-is, and how often it's polled for completion. The wait ends
# early the moment the job reports "Stopped", so this ceiling is only
# ever reached when a plugin never answers — dead time, not work. The
# results collected so far are returned either way, so lowering it drops
# stragglers rather than losing what already arrived.
SEARCH_CEILING_SECONDS = float(os.environ.get("SEARCH_CEILING_SECONDS", "30"))
SEARCH_POLL_INTERVAL_SECONDS = float(os.environ.get("SEARCH_POLL_INTERVAL_SECONDS", "2"))

# How often the worker checks whether an automatic request-history
# retention policy is set (a Settings-panel dropdown) and, if so, purges
# terminal requests older than it. Retention is day-granularity, so an
# hourly check is plenty responsive without adding meaningful load.
RETENTION_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("RETENTION_CLEANUP_INTERVAL_SECONDS", "3600"))

# -- Stage 2 pipeline defaults. Plain module constants for now; Stage 7
#    moves these into the settings table, editable from the Settings panel. --

CATEGORY = "movies"

# Pass one (relevance gate) + pass two (quality score) share the same
# resolution tier table. MIN_RESOLUTION is a *floor*, not a fixed gate: any
# tier at or above it can pass, and resolution is the highest-weighted
# signal in pass two, so a 2160p candidate always outranks a 1080p one and
# so on down. The floor sits at the lowest tier: a request means "the best
# copy that exists", and the ranking decides what that is. The floor is
# still a real gate — a release with no resolution or source word at all
# can't be judged and is never picked. Tests raise it to exercise the gate.
RESOLUTION_TIERS = (
    (4, ("2160p", "4k", "uhd")),
    (3, ("1080p", "fullhd", "fhd")),
    (2, ("720p", "hd")),
    # Standard-definition releases rarely carry a "480p" token at all —
    # they say what they were ripped from instead (TVRip, DVDRip, HDTV
    # XviD…). Those source words count as the SD tier so an "Anything"
    # (480p) floor genuinely accepts them; any explicit higher token in
    # the same name still wins, since tiers are matched highest first.
    (1, ("480p", "sd", "dvdrip", "dvdr", "tvrip", "hdtv", "pdtv", "sdtv", "dsr", "xvid")),
)
MIN_RESOLUTION = "480p"

YEAR_TOLERANCE = 1
LANGUAGE_ALLOWLIST: tuple[str, ...] = ()  # empty = no restriction, OR semantics (any one qualifies)
LANGUAGE_BLOCKLIST: tuple[str, ...] = ()  # empty = nothing blocked
# AND semantics, distinct from LANGUAGE_ALLOWLIST's OR — every language
# listed must be present (e.g. a dual-audio release needs English *and*
# French together, not just one or the other).
LANGUAGE_REQUIRED: tuple[str, ...] = ()  # empty = nothing required

# Cam/telesync/screener markers — always excluded, unlike the language
# lists (which default open). A recent-release real-world test (Spider-Man:
# Brand New Day, 2026-09-04) found the only candidates for a movie just out
# of theaters were vague, unlabeled "4K (Small File)"-style uploads with no
# real source/codec info at all — this list doesn't catch that specific
# case (nothing to match against), but does stop an explicitly-tagged
# bootleg from passing the resolution gate at face value.
CAM_BLOCKLIST: tuple[str, ...] = (
    "cam",
    "hdcam",
    "camrip",
    "hdts",
    "ts",
    "telesync",
    "tc",
    "telecine",
    "scr",
    "screener",
    "dvdscr",
    "r5",
    "r6",
    "workprint",
)

# Non-video payload markers — a release whose real payload is an archive
# or, worse, an executable, rather than a raw video file. Confirmed live
# twice on the same Ted Lasso S04 subscription (2026-09-08): first a
# .zipx result with no video file for media_organizer.select_video_file()
# to find and organize; then, after archives alone were blocked, a
# "Ted.Lasso.S04E06.1080p.ATVP.WEB.H264-NTb.exe" result — a fake-release
# executable, the classic disguised-malware-download shape, not merely an
# organizing gap. Rejected at the same pass-one relevance-gate stage as
# the cam blocklist above — before the torrent is ever added — rather
# than only discovered post-download, since select_video_file's own
# VIDEO_EXTENSIONS check can't run until the torrent's file list actually
# exists, and by then the executable may already be sitting on disk.
NON_VIDEO_BLOCKLIST: tuple[str, ...] = (
    "zip",
    "zipx",
    "rar",
    "7z",
    "exe",
    "msi",
    "scr",
    "bat",
    "cmd",
    "com",
    "vbs",
    "js",
    "jar",
    "apk",
    "dmg",
    "pkg",
    "iso",
)

# Pass two (quality score): resolution > source > codec > seeder health >
# container, known-and-healthy-seeders/raw-count/size as final tiebreakers.
# Seeder health outranking container (not the reverse) was a deliberate
# real-world-tested call — see score.py's weight-declaration comment. Each
# tier list is (score, [phrase, ...]) — the phrase list covers real-world
# spelling variants seen in Stage 0's live sample (e.g. "WEB-DL" vs
# "WEBDL", "H.265" vs "x265").
SOURCE_TIERS = (
    (5, ("remux",)),
    (4, ("bluray", "blu-ray", "bdrip", "brrip", "bd-rip")),
    (3, ("web-dl", "webdl")),
    (2, ("webrip", "web-rip")),
    (1, ("hdtv",)),
)
CODEC_TIERS = (
    (2, ("hevc", "x265", "h265", "h.265")),
    (1, ("avc", "x264", "h264", "h.264")),
)
CONTAINER_TIERS = (
    (2, ("mkv",)),
    (1, ("mp4",)),
)

# Seeders stay deliberately *not* the primary key (Stage 0 found ~50% of
# real rows report nbSeeders == -1/unknown, almost all from one high-volume
# plugin — scoring seeders-first would silently starve results from it).
# But a bare "not completely dead" floor of 1 doesn't protect against a
# genuinely slow download, so MIN_SEEDERS is a real practical floor, and
# within one resolution SEEDER_TIERS outranks source, codec and container:
# the healthiest copy of the resolution you asked for is the one that
# actually arrives.
#
# The ladder is deliberately fine. At 10/30/100 it was coarse enough that
# everything above 100 tied, and inside a tier source and codec decided —
# so a 2160p REMUX on 100 seeders beat a 2160p WEB-DL on 5000, and a
# 31-seeder x265 beat a 99-seeder x264 (live 2026-09-21, The Empty Man:
# "it chose a 13 seed one over the 62 seed one"). Roughly doubling each
# step means a real health gap always crosses a tier, while counts close
# enough to be noise — 100 against 105 — stay level and let genuine
# quality differences decide, which is the behaviour the coarse ladder
# was reaching for in the first place.
#
# Unknown seeders score as tier 1 (viable, not punished — Stage 0's
# "unknown != zero"). Since Score.sort_key ranks known-swarm candidates
# above unknown ones outright, that value only ever compares unknowns
# against each other, where it is uniform.
MIN_SEEDERS = 10  # nbSeeders == -1 (unknown) always passes; a known count must clear this
SEEDER_TIERS = (
    (7, 1000),
    (6, 500),
    (5, 250),
    (4, 100),
    (3, 50),
    (2, 25),
    (1, 10),
)
UNKNOWN_SEEDERS_SCORE = 1

MIN_SIZE_GB = 1
MAX_SIZE_GB = 150

# A pick whose swarm never materialises. worker._check_downloading only
# ever recognised two outcomes — the torrent vanished, or it finished —
# so "qBittorrent still has it, and it has downloaded nothing from
# nobody" matched neither and simply sat there (live 2026-09-20: three
# rows stalled at 0%, the oldest two days in). Past the grace period
# such a pick is rejected like a manually-flagged bad copy and the
# search runs again without it.
#
# The grace is generous on purpose: a healthy torrent finds seeds in
# minutes, but a tracker announce or DHT bootstrap can be slow, and the
# cost of being wrong is discarding a download that would have started.
STALL_GRACE_SECONDS = int(os.environ.get("STALL_GRACE_SECONDS", "1800"))
# Attempts *per request*, counting the original. Bounded so a title with
# nothing alive behind it fails visibly instead of cycling forever.
STALL_MAX_ATTEMPTS = int(os.environ.get("STALL_MAX_ATTEMPTS", "3"))

# Plugins known (from Stage 0's live spike) to return error/config rows
# disguised as results rather than real torrents — excluded outright.
PLUGIN_DISTRUST = frozenset({"jackett"})

# -- Stage 10: episode-aware match, score & download. --

# Separate from CATEGORY ("movies"): this string is both qBittorrent's
# search-plugin category filter (searching under "movies" would miss real
# TV releases entirely) and the torrents_add() category label, so TV
# downloads need their own value, not a Settings-panel choice yet — same
# "plain module constant ahead of a settings table" pattern CATEGORY itself
# started as in Stage 2.
TV_CATEGORY = "tv"

# -- Stage 6: git-based deploy & update. --

# Root of the deployed-copy git clone (contains .git) as seen from inside
# the backend container — distinct from DB_PATH's persistent-data volume
# and from the `backend/app` bind mount uvicorn --reload watches. Only
# the real NAS deployment mounts this (see truenas/custom-app-compose.yaml);
# local Compose has no such clone, so /api/admin/deploy fails loudly there
# instead of silently doing nothing.
DEPLOY_REPO_PATH = Path(os.environ.get("DEPLOY_REPO_PATH", "/repo"))

# Path to the read-only GitHub deploy key, mounted as a secret file outside
# the git-tracked tree — never committed, never visible via `docker
# inspect` (only the mount target path is). When unset, `git pull` runs
# with whatever SSH/credential setup the container already has.
GIT_SSH_KEY_PATH = os.environ.get("GIT_SSH_KEY_PATH")

DEPLOY_TIMEOUT_SECONDS = int(os.environ.get("DEPLOY_TIMEOUT_SECONDS", "60"))

# Frontend migration Part B: `npm ci` + a Vite build, run after every
# successful git pull (see deploy.py's run_git_pull) — meaningfully
# slower than a plain `git pull`, so its own separate, more generous
# ceiling rather than sharing DEPLOY_TIMEOUT_SECONDS's 60s default.
DEPLOY_BUILD_TIMEOUT_SECONDS = int(os.environ.get("DEPLOY_BUILD_TIMEOUT_SECONDS", "180"))

# -- Stage 11: post-download file organization & Plex library layout. --

# This container's path to the root of Plex's TV library, on the same
# underlying dataset qBittorrent's "tv" category downloads into (required
# for the hardlink in media_organizer.py to work at all — a hardlink can't
# cross filesystems). Real host path is a NAS-deployment detail pinned down
# in truenas/custom-app-compose.yaml, not here; local/test runs default to
# a plainly-fake path since nothing local exercises this without the mount.
TV_LIBRARY_ROOT = Path(os.environ.get("TV_LIBRARY_ROOT", "/tv-library"))

# qBittorrent's OWN path to that exact same underlying folder. This app and
# qBittorrent run as separate containers, each with their own bind mount of
# the same host directory under a name of their own choosing — qBittorrent
# reports `save_path` in its own container's namespace (e.g. "/media/TV
# Shows"), which is a completely different string from this container's
# own TV_LIBRARY_ROOT ("/tv-library") for the identical bytes on disk.
# media_organizer.py needs both: this to recognize/strip qBittorrent's
# prefix, TV_LIBRARY_ROOT to rebuild the equivalent path this container can
# actually open. Unset by default — translation is then a no-op, matching
# every environment (tests, this workstation) that doesn't run two
# containers pointed at one shared mount.
QBIT_TV_SAVE_PATH = os.environ.get("QBIT_TV_SAVE_PATH")

# Extensions media_organizer.select_video_file() will consider an episode's
# real video file. Anything else in a torrent (.nfo, .srt, .txt) is
# ignored outright, not just deprioritized.
#
# .m2ts is the Blu-ray/BDMV stream extension (BDMV/STREAM/00000.m2ts) —
# confirmed live: a "Tron.Legacy.2010.2160p.BDMV.Remux...-DaTmoSX" release's
# actual video payload was an .m2ts file, missing this extension made
# select_video_file() see zero candidates, and the resulting NoVideoFileError
# got the whole torrent purged as a fake release even though it was a
# legitimate, complete BDMV remux.
VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".wmv", ".mov"})

# -- Stage 12: subscriptions, scheduling & per-episode dedup. --

# Defaults for `tv_settings.py`'s `TVScheduleSettings` — a household can
# override all of these from the Settings panel (`GET`/`PUT
# /api/settings/tv`), same "plain config.py default, real value lives in
# the settings table" pattern Stage 7 established for PipelineSettings.
# How often worker.py's `_watch_shows` loop rechecks every "watching" show
# for newly-aired episodes.
SHOW_CHECK_INTERVAL_HOURS = 6.0

# How many hours past midnight on an episode's TMDB air_date before
# tv_resolve.aired_episode_numbers()/season_is_complete() treat it as
# genuinely aired. TMDB only ever gives a date, never a release time, so
# without this a same-day air_date makes an episode "due" for search the
# instant the calendar date rolls over — real-world case: a Ted Lasso
# S04E06 recheck that fired hours before the actual release, into a
# window with no legitimate torrent yet, where the only results were a
# .zipx with no video file and, worse, a fake-release .exe (2026-09-08).
# A household can raise this if it wants a wider safety margin, or drop
# it to 0 to restore the original exact-date behavior.
EPISODE_AIR_BUFFER_HOURS = 12.0

# Episode auto-recheck (retry a stuck episode, or look for a better release
# once one is already downloaded): off by default — this is new, automatic,
# unattended behavior (including auto-replacing an already-downloaded file
# on an upgrade), so it's opt-in rather than silently changing what a
# household that never touches Settings already gets.
EPISODE_RECHECK_ENABLED = False
EPISODE_RECHECK_INTERVAL_HOURS = 1.0
EPISODE_RECHECK_MAX_ATTEMPTS = 3  # 0 = keep rechecking indefinitely

# How often worker.py's `_watch_episode_rechecks` loop wakes up to ask "is
# anything due for a recheck right now" — a fixed polling granularity, not
# itself a Settings-panel value (the *interval* between actual rechecks is
# the adjustable knob; this is just how finely that's measured).
EPISODE_RECHECK_POLL_INTERVAL_SECONDS = int(os.environ.get("EPISODE_RECHECK_POLL_INTERVAL_SECONDS", "900"))

# -- Stage 13.x: post-organize source cleanup. --

# Once a completed torrent's file(s) have been hardlinked/copied into
# Plex's library layout (organize_episode/organize_movie/organize_pack),
# the worker also removes the *original* torrent — qBittorrent queue entry
# and its own downloaded copy — after this grace delay, once a final
# re-check confirms every organized copy is genuinely still in place. A
# hardlinked organized copy shares the same underlying bytes as the
# torrent's own file, so this never loses real data; the copy-fallback
# case (hardlink unsupported — see media_organizer.py's `_link_or_copy`)
# already made a fully independent copy at organize time, so the same
# holds there too. This is a deliberate tradeoff against continuing to
# seed that torrent, made at the user's explicit request to keep the
# library free of duplicate raw-release folders rather than leaving both
# copies sitting on disk indefinitely (this project's earlier default,
# and still what Stage 11/12/13's own live validation sessions left
# behind on the real NAS).
SOURCE_CLEANUP_DELAY_SECONDS = int(os.environ.get("SOURCE_CLEANUP_DELAY_SECONDS", "60"))

# How many sweeps may find an organized copy unreadable before cleanup
# of its original is abandoned. The original must not be deleted while
# any organized copy is missing — it may be the only copy left — but
# treating the first look as final meant a momentarily unreadable path
# (a NAS busy with several downloads, a file mid-move during a Plex
# scan) orphaned the source folder permanently and silently. Six
# attempts at SOURCE_CLEANUP_DELAY_SECONDS apart is several minutes of
# grace before giving up, and giving up now says so at warning level.
SOURCE_CLEANUP_VERIFY_ATTEMPTS = int(os.environ.get("SOURCE_CLEANUP_VERIFY_ATTEMPTS", "6"))

# Post-fix (originally fire-and-forget, in-memory only — lost its work
# permanently if the backend restarted or the one-shot delete failed
# anywhere in the delay window, a real gap found live): how often
# worker.py's `_watch_source_cleanup` loop wakes up to check "is any
# organized request's cleanup due right now" — a fixed polling
# granularity, same relationship to SOURCE_CLEANUP_DELAY_SECONDS that
# EPISODE_RECHECK_POLL_INTERVAL_SECONDS has to *_INTERVAL_HOURS above.
# Also reused as the retry backoff after a failed cleanup attempt.
SOURCE_CLEANUP_POLL_INTERVAL_SECONDS = int(os.environ.get("SOURCE_CLEANUP_POLL_INTERVAL_SECONDS", "15"))

# This container's path to Plex's movie library root — same dataset
# qBittorrent's "movies" category downloads into, for the same hardlink-
# needs-one-filesystem reason as TV_LIBRARY_ROOT. Movies were never
# organized before this (Stages 0-8 left qBittorrent's own raw download
# folder as the final resting place); this only renames the *folder* a
# movie sits in to a Plex-recognized `<Title> (<year>) {tmdb-<id>}` shape,
# never the file itself, per the user's explicit ask.
MOVIE_LIBRARY_ROOT = Path(os.environ.get("MOVIE_LIBRARY_ROOT", "/movie-library"))

# qBittorrent's own path to that same movie dataset — same two-container
# path-namespace mismatch QBIT_TV_SAVE_PATH exists for, mirrored here for
# movies. Unset by default for the same reason.
QBIT_MOVIE_SAVE_PATH = os.environ.get("QBIT_MOVIE_SAVE_PATH")

# -- Stage 14: home hero carousel trailers. --

# Self-hosted cache for home hero trailer clips (trailers.py). Embedding
# YouTube's own player left its "Cards" overlay showing through — a
# creator-configured interactive layer that can appear at any timestamp,
# tied to no player state and suppressible by no embed parameter or JS API
# call — so instead the clip is downloaded once via yt-dlp and served
# directly as a plain file, at the user's explicit request after this
# limitation was explained. Same DB_PATH pattern/volume: resolves to
# /app/data/trailers in Docker, reusing the backend-data volume already
# mounted at /app/data rather than needing a new one.
TRAILER_CACHE_DIR = Path(os.environ.get("TRAILER_CACHE_DIR", Path(__file__).resolve().parent.parent / "data" / "trailers"))

# Most-recently-used trailer files to keep on disk; enforce_downloaded()
# evicts the oldest beyond this on every new download.
# Every hero slide carries a trailer now, not just the front two, and
# three pages have a hero (home, movies, tv), so a single pass over the
# app can touch fifteen distinct titles. At 20 the cache evicted titles
# that were still on screen and re-downloaded them on the next loop.
TRAILER_CACHE_MAX_FILES = int(os.environ.get("TRAILER_CACHE_MAX_FILES", "60"))

TRAILER_DOWNLOAD_TIMEOUT_SECONDS = int(os.environ.get("TRAILER_DOWNLOAD_TIMEOUT_SECONDS", "45"))

# The hero prefers the shortest preview that is still a preview. Under
# this many seconds a clip is a social-media sting rather than a trailer
# — measured on TMDB's own data, the sub-45s "Teasers" on file are
# things like Inside Out 2's 15s "#1 Movie is Certified Fresh" and
# Mission: Impossible's 5s "Your mission begins NOW!". Above the floor,
# shortest wins; if a title has nothing above it, the longest of what it
# does have is used rather than nothing.
TRAILER_MIN_SECONDS = int(os.environ.get("TRAILER_MIN_SECONDS", "45"))
# How many of a title's candidates are measured before choosing. Each
# costs one yt-dlp metadata fetch, run in parallel and only on a cold
# cache, so this bounds the wait on a title with a dozen promos on file.
TRAILER_PROBE_LIMIT = int(os.environ.get("TRAILER_PROBE_LIMIT", "6"))

# -- Stage 15: strip embedded cover-art from organized files. --

# Some scene releases embed a custom cover image directly inside the video
# container (an mp4/mkv "attached picture" stream, or an MKV attachment
# tagged as an image) — Plex's scanner reads this straight from the file
# itself, so it shows up as the item's poster/art regardless of what TMDB
# metadata Plex would otherwise use, and no external image file needs to
# exist alongside it for that to happen. media_organizer.py's ffprobe
# check (read-only, near-instant even on a huge file) needs its own
# timeout distinct from the actual ffmpeg remux below.
FFPROBE_TIMEOUT_SECONDS = int(os.environ.get("FFPROBE_TIMEOUT_SECONDS", "15"))

# The actual strip is a stream-copy remux (`-c copy`, no re-encode) of
# every stream except the detected embedded-artwork one(s) — no decoding
# happens, so even a large file finishes in well under a minute in
# practice, but this is a real file-sized operation (unlike the ffprobe
# read above), hence the far more generous ceiling.
FFMPEG_STRIP_TIMEOUT_SECONDS = int(os.environ.get("FFMPEG_STRIP_TIMEOUT_SECONDS", "600"))


# -- Settings › Plex: the library folders can be changed from the UI.
#    An env var still wins (same convention as the qBittorrent fields);
#    otherwise a saved value replaces the module constant at startup and
#    again whenever the setting is saved, so media_organizer's callers,
#    which read `config.MOVIE_LIBRARY_ROOT` at call time, follow along. --

_MOVIE_ROOT_ENV = os.environ.get("MOVIE_LIBRARY_ROOT")
_TV_ROOT_ENV = os.environ.get("TV_LIBRARY_ROOT")


def library_root_source() -> str:
    return "env" if (_MOVIE_ROOT_ENV or _TV_ROOT_ENV) else "db"


def apply_library_overrides(store: "RequestStore") -> None:
    global MOVIE_LIBRARY_ROOT, TV_LIBRARY_ROOT
    settings = store.get_settings()
    if not _MOVIE_ROOT_ENV and settings.get("movie_library_root"):
        MOVIE_LIBRARY_ROOT = Path(settings["movie_library_root"])
    if not _TV_ROOT_ENV and settings.get("tv_library_root"):
        TV_LIBRARY_ROOT = Path(settings["tv_library_root"])
