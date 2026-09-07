from app import config
from app.pack_score import (
    has_any_episode_token,
    has_complete_series_marker,
    has_season_range_marker,
    has_season_token,
    parse_season_range,
    passes_season_pack_gate,
    passes_season_range_pack_gate,
    passes_series_pack_gate,
)
from app.pipeline_settings import PipelineSettings
from app.tv_resolve import ShowIdentity

LANTERNS = ShowIdentity(
    tmdb_id=95350,
    title="Lanterns",
    original_title="Lanterns",
    variants=["Lanterns"],
)


# ---------------------------------------------------------------------------
# Season pack gate — the mirror image of tv_score's per-episode gate: a lone
# season token with no episode token must be *accepted* here, the exact
# shape Stage 10's own gate rejects.
# ---------------------------------------------------------------------------


def test_passes_season_pack_gate_accepts_season_only_token():
    assert passes_season_pack_gate("Lanterns.S01.2160p.WEB-DL.mkv", LANTERNS, 1) is True


def test_passes_season_pack_gate_accepts_season_phrase():
    assert passes_season_pack_gate("Lanterns Season 01 2160p WEB-DL", LANTERNS, 1) is True


def test_passes_season_pack_gate_rejects_wrong_season():
    assert passes_season_pack_gate("Lanterns.S02.2160p.WEB-DL.mkv", LANTERNS, 1) is False


def test_passes_season_pack_gate_rejects_wrong_show():
    assert passes_season_pack_gate("Some.Other.Show.S01.2160p.WEB-DL.mkv", LANTERNS, 1) is False


def test_passes_season_pack_gate_rejects_a_real_single_episode_release():
    # A genuine single-episode release also carries "s01" as a substring
    # token, but also carries an episode token — must not be admitted here,
    # since that's exactly what the per-episode pipeline already handles.
    assert passes_season_pack_gate("Lanterns.S01E04.2160p.WEB-DL.mkv", LANTERNS, 1) is False


def test_passes_season_pack_gate_rejects_below_default_floor():
    assert passes_season_pack_gate("Lanterns.S01.1080p.WEB-DL.mkv", LANTERNS, 1) is False


def test_passes_season_pack_gate_rejects_hdcam():
    assert passes_season_pack_gate("Lanterns.S01.2160p.HDCAM.mkv", LANTERNS, 1) is False


def test_passes_season_pack_gate_with_explicit_settings_language_blocklist():
    blocklist_settings = PipelineSettings(
        category="movies",
        min_resolution="2160p",
        min_size_gb=1,
        max_size_gb=150,
        language_allowlist=(),
        language_blocklist=("french",),
    )
    assert passes_season_pack_gate("Lanterns.S01.2160p.WEB-DL.FRENCH.mkv", LANTERNS, 1, blocklist_settings) is False


def test_passes_season_pack_gate_lowering_floor_admits_1080p(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    assert passes_season_pack_gate("Lanterns.S01.1080p.WEB-DL.mkv", LANTERNS, 1) is True


# ---------------------------------------------------------------------------
# Complete-series gate — requires an explicit marker, not just "no episode
# token" (which the season-pack gate above would also satisfy).
# ---------------------------------------------------------------------------


def test_passes_series_pack_gate_accepts_complete_series_marker():
    assert passes_series_pack_gate("Lanterns.Complete.Series.2160p.WEB-DL.mkv", LANTERNS) is True


def test_passes_series_pack_gate_accepts_the_complete_series_phrase():
    assert passes_series_pack_gate("Lanterns The Complete Series 2160p WEB-DL", LANTERNS) is True


def test_passes_series_pack_gate_rejects_a_lone_season_pack():
    # Season-pack-shaped (no episode token) is not enough on its own — a
    # real, explicit complete-series marker is required, not merely the
    # absence of an episode token (which a single stray season pack file
    # would also satisfy).
    assert passes_series_pack_gate("Lanterns.S01.2160p.WEB-DL.mkv", LANTERNS) is False


def test_passes_series_pack_gate_rejects_wrong_show():
    assert passes_series_pack_gate("Some.Other.Show.Complete.Series.2160p.WEB-DL.mkv", LANTERNS) is False


def test_passes_series_pack_gate_rejects_below_default_floor():
    assert passes_series_pack_gate("Lanterns.Complete.Series.1080p.WEB-DL.mkv", LANTERNS) is False


def test_passes_series_pack_gate_rejects_hdcam():
    assert passes_series_pack_gate("Lanterns.Complete.Series.2160p.HDCAM.mkv", LANTERNS) is False


# ---------------------------------------------------------------------------
# Season-range gate (Stage 14.x) — an explicit multi-season bundle marker
# ("S01-S03", "Seasons 1-3"), distinct from both the single-season and
# complete-series shapes above.
# ---------------------------------------------------------------------------


def test_passes_season_range_pack_gate_accepts_dashed_season_tokens():
    assert passes_season_range_pack_gate("Lanterns.S01-S03.2160p.WEB-DL.mkv", LANTERNS, 1, 3) is True


def test_passes_season_range_pack_gate_accepts_seasons_phrase():
    assert passes_season_range_pack_gate("Lanterns Seasons 1-3 2160p WEB-DL", LANTERNS, 1, 3) is True


def test_passes_season_range_pack_gate_accepts_a_wider_real_release():
    # A real release bundling more than what's strictly needed (tagged
    # S01-S04 when only seasons 1-3 were unhandled) still satisfies the
    # request — organize_pack only ever files what it actually finds.
    assert passes_season_range_pack_gate("Lanterns.S01-S04.2160p.WEB-DL.mkv", LANTERNS, 1, 3) is True


def test_passes_season_range_pack_gate_rejects_a_narrower_range():
    assert passes_season_range_pack_gate("Lanterns.S02-S03.2160p.WEB-DL.mkv", LANTERNS, 1, 3) is False


def test_passes_season_range_pack_gate_rejects_a_lone_season():
    assert passes_season_range_pack_gate("Lanterns.S01.2160p.WEB-DL.mkv", LANTERNS, 1, 3) is False


def test_passes_season_range_pack_gate_rejects_an_episode_shaped_result():
    # A range marker alone isn't enough if the same filename also names a
    # specific episode — that's a single-episode release, not a pack, even
    # if it happens to mention a season range somewhere in its own title.
    assert passes_season_range_pack_gate("Lanterns.S01-S03.S01E04.2160p.WEB-DL.mkv", LANTERNS, 1, 3) is False


def test_passes_season_range_pack_gate_rejects_wrong_show():
    assert passes_season_range_pack_gate("Some.Other.Show.S01-S03.2160p.WEB-DL.mkv", LANTERNS, 1, 3) is False


def test_passes_season_range_pack_gate_rejects_below_default_floor():
    assert passes_season_range_pack_gate("Lanterns.S01-S03.1080p.WEB-DL.mkv", LANTERNS, 1, 3) is False


def test_passes_season_range_pack_gate_rejects_hdcam():
    assert passes_season_range_pack_gate("Lanterns.S01-S03.2160p.HDCAM.mkv", LANTERNS, 1, 3) is False


def test_parse_season_range_dashed_tokens():
    assert parse_season_range(["lanterns", "s01", "s03", "2160p"]) == (1, 3)


def test_parse_season_range_seasons_phrase():
    assert parse_season_range(["lanterns", "seasons", "1", "3", "2160p"]) == (1, 3)


def test_parse_season_range_none_for_a_lone_season():
    assert parse_season_range(["lanterns", "s01", "2160p"]) is None


def test_parse_season_range_none_when_start_not_less_than_end():
    assert parse_season_range(["lanterns", "s03", "s01", "2160p"]) is None


def test_parse_season_range_none_for_unrelated_numbers():
    assert parse_season_range(["lanterns", "2024", "2160p"]) is None


def test_has_season_range_marker_true_when_claimed_range_covers_request():
    assert has_season_range_marker(["lanterns", "s01", "s04"], 1, 3) is True


def test_has_season_range_marker_false_when_claimed_range_is_narrower():
    assert has_season_range_marker(["lanterns", "s02", "s03"], 1, 3) is False


# ---------------------------------------------------------------------------
# Helper functions directly.
# ---------------------------------------------------------------------------


def test_has_any_episode_token_detects_contiguous_shape():
    assert has_any_episode_token(["lanterns", "s01e04", "2160p"]) is True


def test_has_any_episode_token_detects_dotted_shape():
    assert has_any_episode_token(["lanterns", "s01", "e04", "2160p"]) is True


def test_has_any_episode_token_false_for_season_only():
    assert has_any_episode_token(["lanterns", "s01", "complete", "2160p"]) is False


def test_has_season_token_matches_zero_padded_and_phrase_forms():
    assert has_season_token(["lanterns", "s01", "2160p"], 1) is True
    assert has_season_token(["lanterns", "season", "1", "2160p"], 1) is True
    assert has_season_token(["lanterns", "season", "01", "2160p"], 1) is True


def test_has_complete_series_marker_true_and_false():
    assert has_complete_series_marker(["lanterns", "complete", "series", "2160p"]) is True
    assert has_complete_series_marker(["lanterns", "s01", "2160p"]) is False
