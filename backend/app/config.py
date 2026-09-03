import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

QBIT_HOST = os.environ.get("QBIT_HOST", "192.168.0.133")
QBIT_PORT = int(os.environ.get("QBIT_PORT", "30024"))
QBIT_USERNAME = os.environ.get("QBIT_USERNAME", "")
QBIT_PASSWORD = os.environ.get("QBIT_PASSWORD", "")

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
