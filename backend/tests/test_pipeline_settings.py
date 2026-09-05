from app import config
from app.db import RequestStore
from app.pipeline_settings import (
    PipelineSettings,
    is_valid_min_resolution,
    resolve_pipeline_settings,
    settings_from_raw,
)


def test_from_config_matches_config_defaults():
    settings = PipelineSettings.from_config()
    assert settings.category == config.CATEGORY
    assert settings.min_resolution == config.MIN_RESOLUTION
    assert settings.min_size_gb == config.MIN_SIZE_GB
    assert settings.max_size_gb == config.MAX_SIZE_GB
    assert settings.language_allowlist == tuple(config.LANGUAGE_ALLOWLIST)
    assert settings.language_blocklist == tuple(config.LANGUAGE_BLOCKLIST)


def test_is_valid_min_resolution_accepts_known_tiers():
    assert is_valid_min_resolution("2160p") is True
    assert is_valid_min_resolution("1080P") is True  # case-insensitive, via normalize_text


def test_is_valid_min_resolution_rejects_unknown_tier():
    assert is_valid_min_resolution("8k") is False


def test_resolve_pipeline_settings_falls_back_to_defaults_when_store_is_empty():
    store = RequestStore(":memory:")
    settings = resolve_pipeline_settings(store)
    assert settings == PipelineSettings.from_config()


def test_resolve_pipeline_settings_overlays_saved_values():
    store = RequestStore(":memory:")
    store.update_settings(
        {
            "category": "family-movies",
            "min_resolution": "1080p",
            "min_size_gb": 2,
            "max_size_gb": 80,
            "language_allowlist": ["english"],
            "language_blocklist": ["french"],
        }
    )
    settings = resolve_pipeline_settings(store)
    assert settings.category == "family-movies"
    assert settings.min_resolution == "1080p"
    assert settings.min_size_gb == 2
    assert settings.max_size_gb == 80
    assert settings.language_allowlist == ("english",)
    assert settings.language_blocklist == ("french",)


def test_resolve_pipeline_settings_null_field_falls_back_to_default():
    store = RequestStore(":memory:")
    store.update_settings({"min_resolution": "1080p"})
    store.update_settings({"min_resolution": None})  # e.g. a Settings-panel reset
    settings = resolve_pipeline_settings(store)
    assert settings.min_resolution == config.MIN_RESOLUTION


def test_settings_from_raw_matches_resolve_pipeline_settings():
    store = RequestStore(":memory:")
    store.update_settings({"category": "family-movies"})
    assert settings_from_raw(store.get_settings()) == resolve_pipeline_settings(store)
