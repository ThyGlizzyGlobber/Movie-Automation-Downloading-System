import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

QBIT_HOST = os.environ.get("QBIT_HOST", "192.168.0.133")
QBIT_PORT = int(os.environ.get("QBIT_PORT", "30024"))
QBIT_USERNAME = os.environ.get("QBIT_USERNAME", "")
QBIT_PASSWORD = os.environ.get("QBIT_PASSWORD", "")

# -- Stage 3: API/persistence layer. --

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).resolve().parent.parent / "data" / "app.db"))

# How often the download watcher polls qBittorrent for requests already
# added and sitting in "downloading", to flip them to "complete". Doesn't
# hold up the pipeline lock — see worker.py.
DOWNLOAD_POLL_INTERVAL_SECONDS = int(os.environ.get("DOWNLOAD_POLL_INTERVAL_SECONDS", "10"))

# After add_torrent(), how many times (and how far apart) pipeline.py
# retries diffing existing_torrent_hashes() to find the new torrent's hash.
# A direct-.torrent-URL result isn't indexed by qBittorrent instantly —
# confirmed live during Stage 3 validation (torlock winner, Big Buck Bunny).
HASH_CAPTURE_ATTEMPTS = 5
HASH_CAPTURE_INTERVAL_SECONDS = 1.0

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
# tier at or above it can pass, and resolution is scored as the
# highest-weighted tier in pass two — so within a floor of "1080p", a 2160p
# candidate always outranks a 1080p one, but a 1080p candidate is still
# accepted when nothing higher qualifies. Default floor "2160p" reproduces
# Stage 2's original hard-4K-only behavior; lowering it (a Stage 7 setting)
# is what enables the "fall back to 1080p if no 4K result" behavior.
RESOLUTION_TIERS = (
    (4, ("2160p", "4k", "uhd")),
    (3, ("1080p", "fullhd", "fhd")),
    (2, ("720p", "hd")),
    (1, ("480p", "sd")),
)
MIN_RESOLUTION = "2160p"

YEAR_TOLERANCE = 1
LANGUAGE_ALLOWLIST: tuple[str, ...] = ()  # empty = no restriction
LANGUAGE_BLOCKLIST: tuple[str, ...] = ()  # empty = nothing blocked

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
# SEEDER_TIERS gives ranking a modest nudge (weighted below container) so a
# much healthier swarm can still edge out an equally-tiered but barely-alive
# one — without letting seed count override real quality differences.
# Unknown seeders score as tier 1 (viable, not punished — Stage 0's
# "unknown != zero"), same as a candidate just barely clearing the floor.
MIN_SEEDERS = 10  # nbSeeders == -1 (unknown) always passes; a known count must clear this
SEEDER_TIERS = (
    (3, 100),
    (2, 30),
    (1, 10),
)
UNKNOWN_SEEDERS_SCORE = 1

MIN_SIZE_GB = 1
MAX_SIZE_GB = 150

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
VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"})

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
