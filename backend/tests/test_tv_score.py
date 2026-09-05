from app import config
from app.pipeline_settings import PipelineSettings
from app.tv_resolve import ShowIdentity
from app.tv_score import passes_episode_relevance_gate

LANTERNS = ShowIdentity(
    tmdb_id=95350,
    title="Lanterns",
    original_title="Lanterns",
    variants=["Lanterns"],
)


# ---------------------------------------------------------------------------
# Episode identity — the pass-one check that's actually new in Stage 10.
# ---------------------------------------------------------------------------


def test_passes_episode_relevance_gate_happy_path_contiguous_token():
    assert passes_episode_relevance_gate("Lanterns.S01E04.2160p.WEB-DL.mkv", LANTERNS, 1, 4) is True


def test_passes_episode_relevance_gate_happy_path_dotted_token():
    assert passes_episode_relevance_gate("Lanterns.S01.E04.2160p.WEB-DL.mkv", LANTERNS, 1, 4) is True


def test_passes_episode_relevance_gate_happy_path_spaced_token():
    assert passes_episode_relevance_gate("Lanterns S01 E04 2160p WEB-DL", LANTERNS, 1, 4) is True


def test_passes_episode_relevance_gate_rejects_wrong_episode():
    assert passes_episode_relevance_gate("Lanterns.S01E05.2160p.WEB-DL.mkv", LANTERNS, 1, 4) is False


def test_passes_episode_relevance_gate_rejects_wrong_season():
    assert passes_episode_relevance_gate("Lanterns.S02E04.2160p.WEB-DL.mkv", LANTERNS, 1, 4) is False


def test_passes_episode_relevance_gate_rejects_wrong_show():
    assert passes_episode_relevance_gate("Some.Other.Show.S01E04.2160p.WEB-DL.mkv", LANTERNS, 1, 4) is False


def test_passes_episode_relevance_gate_rejects_season_pack_with_no_episode_token():
    # Season packs are out of scope for v1 (Stage 10 "Settled") — a solo
    # "s01" token with nothing identifying a specific episode must be
    # rejected, not silently accepted as a match for episode 1.
    assert passes_episode_relevance_gate("Lanterns.S01.COMPLETE.2160p.WEB-DL.mkv", LANTERNS, 1, 1) is False


def test_passes_episode_relevance_gate_double_digit_season_and_episode():
    assert passes_episode_relevance_gate("Lanterns.S12E34.2160p.WEB-DL.mkv", LANTERNS, 12, 34) is True


def test_passes_episode_relevance_gate_no_year_tolerance_check_needed():
    # A show's first-air-year and an individual episode's air year can
    # legitimately differ — unlike movies, no year token check gates this
    # at all, so a release naming a much later year still passes purely on
    # the episode token.
    assert passes_episode_relevance_gate("Lanterns.2027.S01E04.2160p.WEB-DL.mkv", LANTERNS, 1, 4) is True


# ---------------------------------------------------------------------------
# Reused unchanged from score.py: resolution floor, language filter, cam
# blocklist. Not re-testing their internal logic (score.py's own tests
# already cover that exhaustively) — just confirming they're actually wired
# in here.
# ---------------------------------------------------------------------------


def test_passes_episode_relevance_gate_rejects_below_default_floor():
    assert passes_episode_relevance_gate("Lanterns.S01E04.1080p.WEB-DL.mkv", LANTERNS, 1, 4) is False


def test_passes_episode_relevance_gate_lowering_floor_admits_1080p(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    assert passes_episode_relevance_gate("Lanterns.S01E04.1080p.WEB-DL.mkv", LANTERNS, 1, 4) is True


def test_passes_episode_relevance_gate_rejects_no_recognized_resolution_token():
    assert passes_episode_relevance_gate("Lanterns.S01E04.WEB-DL.mkv", LANTERNS, 1, 4) is False


def test_passes_episode_relevance_gate_rejects_hdcam():
    assert passes_episode_relevance_gate("Lanterns.S01E04.2160p.HDCAM.mkv", LANTERNS, 1, 4) is False


def test_passes_episode_relevance_gate_with_explicit_settings_language_blocklist():
    blocklist_settings = PipelineSettings(
        category="movies",
        min_resolution="2160p",
        min_size_gb=1,
        max_size_gb=150,
        language_allowlist=(),
        language_blocklist=("french",),
    )
    assert (
        passes_episode_relevance_gate(
            "Lanterns.S01E04.2160p.WEB-DL.FRENCH.mkv", LANTERNS, 1, 4, blocklist_settings
        )
        is False
    )
