"""Stage 12.x: adjustable show-check interval and episode auto-recheck
scheduling — the same "plain dataclass + resolve-from-store" pattern Stage
7 established for `PipelineSettings`, kept as its own module since these
are scheduling knobs (how often, how many times), not search/match quality
ones. `worker.py`'s `_watch_shows`/`_watch_episode_rechecks` loops resolve
this fresh every cycle, so a Settings-panel edit takes effect on the very
next wake-up — no restart, no deploy, same as pipeline settings."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app import config

if TYPE_CHECKING:
    from app.db import RequestStore


@dataclass(frozen=True)
class TVScheduleSettings:
    show_check_interval_hours: float
    episode_recheck_enabled: bool
    episode_recheck_interval_hours: float
    episode_recheck_max_attempts: int  # 0 = infinite

    @classmethod
    def from_config(cls) -> "TVScheduleSettings":
        """The as-shipped defaults, read fresh from config.py every call —
        same reasoning as `PipelineSettings.from_config()`."""
        return cls(
            show_check_interval_hours=config.SHOW_CHECK_INTERVAL_HOURS,
            episode_recheck_enabled=config.EPISODE_RECHECK_ENABLED,
            episode_recheck_interval_hours=config.EPISODE_RECHECK_INTERVAL_HOURS,
            episode_recheck_max_attempts=config.EPISODE_RECHECK_MAX_ATTEMPTS,
        )


def settings_from_raw(saved: dict) -> TVScheduleSettings:
    """Overlays a raw settings-table dict on top of config.py's defaults —
    an absent or null key falls back to the default, same convention every
    other Settings-panel field uses. `is not None` (not `or`) throughout:
    `episode_recheck_enabled=False` and `episode_recheck_max_attempts=0`
    (infinite) are both meaningful, deliberately-set values, not "unset"."""
    defaults = TVScheduleSettings.from_config()
    return TVScheduleSettings(
        show_check_interval_hours=(
            saved.get("show_check_interval_hours")
            if saved.get("show_check_interval_hours") is not None
            else defaults.show_check_interval_hours
        ),
        episode_recheck_enabled=(
            saved.get("episode_recheck_enabled")
            if saved.get("episode_recheck_enabled") is not None
            else defaults.episode_recheck_enabled
        ),
        episode_recheck_interval_hours=(
            saved.get("episode_recheck_interval_hours")
            if saved.get("episode_recheck_interval_hours") is not None
            else defaults.episode_recheck_interval_hours
        ),
        episode_recheck_max_attempts=(
            saved.get("episode_recheck_max_attempts")
            if saved.get("episode_recheck_max_attempts") is not None
            else defaults.episode_recheck_max_attempts
        ),
    )


def resolve_tv_settings(store: "RequestStore") -> TVScheduleSettings:
    return settings_from_raw(store.get_settings())
