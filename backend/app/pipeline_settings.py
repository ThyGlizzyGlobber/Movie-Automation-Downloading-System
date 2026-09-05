"""Stage 7: the subset of Stage 2's pipeline defaults that are actually
worth exposing as editable preferences — resolution floor, size range,
language allow/block lists, and the qBittorrent category name. The deep,
real-world-tuned scoring weights (resolution/source/codec/seeder/container
point values — see score.py's weight-declaration comment) are deliberately
NOT exposed here: they were reworked three times against live data during
Stage 2 and a casual edit could silently break the domination-margin
guarantee between tiers. `PipelineSettings` is a plain dataclass so
score.py/pipeline.py stay decoupled from the SQLite settings table (the
"pipeline as a library" principle) — only `resolve_pipeline_settings()`
(called from worker.py/api.py) touches the store.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app import config
from app.normalize import normalize_text

if TYPE_CHECKING:
    from app.db import RequestStore

# Canonical (first-alias) spelling per resolution tier, for the Settings
# panel's dropdown and for validating a PUT — config.RESOLUTION_TIERS stays
# the single source of truth, this just reads its first phrase per tier.
VALID_MIN_RESOLUTIONS: tuple[str, ...] = tuple(phrases[0] for _, phrases in config.RESOLUTION_TIERS)


@dataclass(frozen=True)
class PipelineSettings:
    category: str
    min_resolution: str
    min_size_gb: float
    max_size_gb: float
    language_allowlist: tuple[str, ...]
    language_blocklist: tuple[str, ...]

    @classmethod
    def from_config(cls) -> "PipelineSettings":
        """The as-shipped defaults, read fresh from config.py every call
        (not captured once at import time) so tests that monkeypatch
        `config.MIN_RESOLUTION` etc. keep working unchanged when a caller
        doesn't pass an explicit settings object."""
        return cls(
            category=config.CATEGORY,
            min_resolution=config.MIN_RESOLUTION,
            min_size_gb=config.MIN_SIZE_GB,
            max_size_gb=config.MAX_SIZE_GB,
            language_allowlist=tuple(config.LANGUAGE_ALLOWLIST),
            language_blocklist=tuple(config.LANGUAGE_BLOCKLIST),
        )


def is_valid_min_resolution(value: str) -> bool:
    target = normalize_text(value)
    return any(normalize_text(r) == target for r in VALID_MIN_RESOLUTIONS)


def settings_from_raw(saved: dict) -> PipelineSettings:
    """Overlays a raw settings-table dict on top of config.py's defaults —
    a key that's absent or null falls back to the default, the same
    convention every other Settings-panel field already uses (accent_color,
    request_retention_days). Takes a plain dict rather than a store so
    api.py can validate a *prospective* merge (current row + an incoming
    PUT's patch) before actually writing it — see api.py's
    `set_pipeline_settings` for why that matters (a lone min_size_gb edit
    reverting max_size_gb to its default could otherwise silently invert
    the range)."""
    defaults = PipelineSettings.from_config()
    return PipelineSettings(
        category=saved.get("category") or defaults.category,
        min_resolution=saved.get("min_resolution") or defaults.min_resolution,
        min_size_gb=saved.get("min_size_gb") if saved.get("min_size_gb") is not None else defaults.min_size_gb,
        max_size_gb=saved.get("max_size_gb") if saved.get("max_size_gb") is not None else defaults.max_size_gb,
        language_allowlist=(
            tuple(saved["language_allowlist"])
            if saved.get("language_allowlist") is not None
            else defaults.language_allowlist
        ),
        language_blocklist=(
            tuple(saved["language_blocklist"])
            if saved.get("language_blocklist") is not None
            else defaults.language_blocklist
        ),
    )


def resolve_pipeline_settings(store: "RequestStore") -> PipelineSettings:
    return settings_from_raw(store.get_settings())
