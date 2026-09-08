from app import config
from app.db import RequestStore
from app.tv_settings import TVScheduleSettings, resolve_tv_settings, settings_from_raw


def test_from_config_matches_config_defaults():
    settings = TVScheduleSettings.from_config()
    assert settings.show_check_interval_hours == config.SHOW_CHECK_INTERVAL_HOURS
    assert settings.episode_recheck_enabled == config.EPISODE_RECHECK_ENABLED
    assert settings.episode_recheck_interval_hours == config.EPISODE_RECHECK_INTERVAL_HOURS
    assert settings.episode_recheck_max_attempts == config.EPISODE_RECHECK_MAX_ATTEMPTS
    assert settings.episode_air_buffer_hours == config.EPISODE_AIR_BUFFER_HOURS


def test_resolve_tv_settings_falls_back_to_defaults_when_store_is_empty():
    store = RequestStore(":memory:")
    assert resolve_tv_settings(store) == TVScheduleSettings.from_config()


def test_resolve_tv_settings_overlays_saved_values():
    store = RequestStore(":memory:")
    store.update_settings(
        {
            "show_check_interval_hours": 2,
            "episode_recheck_enabled": True,
            "episode_recheck_interval_hours": 0.5,
            "episode_recheck_max_attempts": 0,
            "episode_air_buffer_hours": 24,
        }
    )
    settings = resolve_tv_settings(store)
    assert settings.show_check_interval_hours == 2
    assert settings.episode_recheck_enabled is True
    assert settings.episode_recheck_interval_hours == 0.5
    assert settings.episode_recheck_max_attempts == 0  # infinite, distinct from "unset"
    assert settings.episode_air_buffer_hours == 24


def test_resolve_tv_settings_null_field_falls_back_to_default():
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_enabled": True})
    store.update_settings({"episode_recheck_enabled": None})  # e.g. a Settings-panel reset
    assert resolve_tv_settings(store).episode_recheck_enabled == config.EPISODE_RECHECK_ENABLED


def test_resolve_tv_settings_max_attempts_zero_is_not_treated_as_unset():
    """A real edge case this module has to get right: 0 means "infinite",
    a deliberate value, not "the field was never set"."""
    store = RequestStore(":memory:")
    store.update_settings({"episode_recheck_max_attempts": 0})
    assert resolve_tv_settings(store).episode_recheck_max_attempts == 0


def test_resolve_tv_settings_air_buffer_zero_is_not_treated_as_unset():
    """0 means "search the instant the air_date arrives" (the original,
    pre-buffer behavior) — a deliberate value, not "unset"."""
    store = RequestStore(":memory:")
    store.update_settings({"episode_air_buffer_hours": 0})
    assert resolve_tv_settings(store).episode_air_buffer_hours == 0


def test_settings_from_raw_matches_resolve_tv_settings():
    store = RequestStore(":memory:")
    store.update_settings({"show_check_interval_hours": 3})
    assert settings_from_raw(store.get_settings()) == resolve_tv_settings(store)
