from app import config
from app.resolve import MediaIdentity
from app.score import (
    dedup_candidates,
    exclude_existing,
    is_trustworthy,
    passes_relevance_gate,
    passes_viability_gate,
    rank_candidates,
    score_candidate,
)

DUNE = MediaIdentity(
    tmdb_id=693134,
    title="Dune: Part Two",
    original_title="Dune: Part Two",
    release_year=2024,
    variants=["Dune: Part Two", "Dune", "Dune: Part Two 2024"],
)


def _result(**overrides) -> dict:
    base = {
        "engineName": "piratebay",
        "fileName": "Dune.Part.Two.2024.2160p.REMUX.mkv",
        "fileUrl": "magnet:?xt=urn:btih:AAAA",
        "fileSize": 40_000_000_000,
        "nbSeeders": 100,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Trust filter
# ---------------------------------------------------------------------------


def test_is_trustworthy_rejects_distrusted_plugin():
    assert is_trustworthy(_result(engineName="jackett", fileUrl="http://127.0.0.1:9117")) is False


def test_is_trustworthy_rejects_localhost_fileurl_even_from_untracked_plugin():
    assert is_trustworthy(_result(engineName="someplugin", fileUrl="http://127.0.0.1:9117")) is False


def test_is_trustworthy_rejects_empty_fileurl():
    assert is_trustworthy(_result(fileUrl="")) is False


def test_is_trustworthy_accepts_magnet():
    assert is_trustworthy(_result(fileUrl="magnet:?xt=urn:btih:AAAA")) is True


def test_is_trustworthy_accepts_real_http_source():
    assert is_trustworthy(_result(fileUrl="https://thepiratebay.org/torrent/12345")) is True


# ---------------------------------------------------------------------------
# Pass one: relevance gate
# ---------------------------------------------------------------------------


def test_passes_relevance_gate_happy_path():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.mkv", DUNE) is True


def test_passes_relevance_gate_rejects_wrong_title():
    assert passes_relevance_gate("Mission.Impossible.Dead.Reckoning.2023.2160p.REMUX.mkv", DUNE) is False


def test_passes_relevance_gate_year_outside_tolerance_fails():
    assert passes_relevance_gate("Dune.Part.Two.2019.2160p.REMUX.mkv", DUNE) is False


def test_passes_relevance_gate_year_within_tolerance_passes():
    assert passes_relevance_gate("Dune.Part.Two.2025.2160p.REMUX.mkv", DUNE) is True


def test_passes_relevance_gate_no_year_token_in_filename_still_passes():
    # Real-world release names often omit the year entirely.
    assert passes_relevance_gate("Dune.Part.Two.UHD.BluRay.2160p.HEVC.REMUX-FraMeSToR", DUNE) is True


def test_passes_relevance_gate_rejects_1080p_below_default_floor():
    assert passes_relevance_gate("Dune.Part.Two.2024.1080p.WEBRip.mkv", DUNE) is False


def test_passes_relevance_gate_accepts_4k_token_as_alternative_to_2160p():
    assert passes_relevance_gate("Dune.Part.Two.2024.4K.HDR.REMUX.mkv", DUNE) is True


def test_passes_relevance_gate_matches_subtitle_free_variant():
    assert passes_relevance_gate("Dune.2024.2160p.REMUX.mkv", DUNE) is True


def test_passes_relevance_gate_rejects_no_recognized_resolution_token_at_all():
    # Fail safe: no floor setting should ever admit a release we can't
    # actually verify the resolution of.
    assert passes_relevance_gate("Dune.Part.Two.2024.REMUX.mkv", DUNE) is False


# ---------------------------------------------------------------------------
# Cam/telesync/screener exclusion
# ---------------------------------------------------------------------------


def test_passes_relevance_gate_rejects_hdcam():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.HDCAM.mkv", DUNE) is False


def test_passes_relevance_gate_rejects_telesync():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.TS.mkv", DUNE) is False


def test_passes_relevance_gate_rejects_screener():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.SCREENER.mkv", DUNE) is False


def test_passes_relevance_gate_rejects_r5():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.R5.mkv", DUNE) is False


def test_passes_relevance_gate_does_not_false_positive_on_dts_audio_tag():
    # "ts" is blocklisted (telesync), but "DTS" is a real, common audio
    # codec tag — it must tokenize as one word ("dts"), never split into a
    # false "ts" match.
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.BluRay.DTS-HD.MA.mkv", DUNE) is True


def test_passes_relevance_gate_still_accepts_a_legit_release():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.BluRay.REMUX.mkv", DUNE) is True


# ---------------------------------------------------------------------------
# Resolution floor — a setting, not a fixed gate. Lowering it is what
# enables "fall back to 1080p if nothing at 4K qualifies."
# ---------------------------------------------------------------------------


def test_lowering_floor_admits_1080p(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    assert passes_relevance_gate("Dune.Part.Two.2024.1080p.WEBRip.mkv", DUNE) is True


def test_lowering_floor_still_admits_2160p(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.mkv", DUNE) is True


def test_lowering_floor_still_rejects_720p(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    assert passes_relevance_gate("Dune.Part.Two.2024.720p.WEBRip.mkv", DUNE) is False


def test_rank_candidates_prefers_2160p_over_1080p_when_floor_allows_both(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    uhd = _result(fileName="Dune.2024.2160p.WEBRip.mkv", fileUrl="magnet:?xt=urn:btih:UHD")
    fhd = _result(fileName="Dune.2024.1080p.REMUX.HEVC.mkv", fileUrl="magnet:?xt=urn:btih:FHD")

    # Even though the 1080p release scores higher on every other tier
    # (remux + hevc vs. an unrecognized source/codec), resolution outranks
    # them all — this is what makes "prefer 4K, only fall back if nothing
    # at 4K qualifies" work without a separate fallback search pass.
    ranked = rank_candidates([fhd, uhd])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:UHD"


def test_rank_candidates_falls_back_to_1080p_when_no_2160p_present(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "1080p")
    fhd = _result(fileName="Dune.2024.1080p.REMUX.HEVC.mkv", fileUrl="magnet:?xt=urn:btih:FHD")

    ranked = rank_candidates([fhd])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:FHD"
    assert ranked[0][1].resolution_score == 3


# ---------------------------------------------------------------------------
# Viability gate (messy nbSeeders/fileSize)
# ---------------------------------------------------------------------------


def test_passes_viability_gate_unknown_seeders_passes():
    assert passes_viability_gate(_result(nbSeeders=-1)) is True


def test_passes_viability_gate_known_zero_seeders_fails():
    assert passes_viability_gate(_result(nbSeeders=0)) is False


def test_passes_viability_gate_below_min_seeders_floor_fails():
    assert passes_viability_gate(_result(nbSeeders=9)) is False


def test_passes_viability_gate_at_min_seeders_floor_passes():
    assert passes_viability_gate(_result(nbSeeders=10)) is True


def test_passes_viability_gate_missing_seeders_key_defaults_to_unknown_and_passes():
    result = _result()
    del result["nbSeeders"]
    assert passes_viability_gate(result) is True


def test_passes_viability_gate_missing_file_size_passes():
    result = _result()
    del result["fileSize"]
    assert passes_viability_gate(result) is True


def test_passes_viability_gate_size_below_range_fails():
    assert passes_viability_gate(_result(fileSize=100_000)) is False


def test_passes_viability_gate_size_above_range_fails():
    assert passes_viability_gate(_result(fileSize=200_000_000_000)) is False


def test_passes_viability_gate_negative_unknown_size_passes():
    assert passes_viability_gate(_result(fileSize=-1)) is True


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_dedup_candidates_by_infohash():
    results = [
        _result(fileUrl="magnet:?xt=urn:btih:AAAA&dn=one"),
        _result(fileUrl="magnet:?xt=urn:btih:AAAA&dn=one-again"),
    ]
    assert len(dedup_candidates(results)) == 1


def test_dedup_candidates_by_name_and_size_when_not_magnet():
    results = [
        _result(fileUrl="https://example.com/a", fileName="Same.Name.mkv", fileSize=1000),
        _result(fileUrl="https://example.com/b", fileName="Same.Name.mkv", fileSize=1000),
    ]
    assert len(dedup_candidates(results)) == 1


def test_dedup_candidates_keeps_distinct_releases():
    results = [
        _result(fileUrl="magnet:?xt=urn:btih:AAAA"),
        _result(fileUrl="magnet:?xt=urn:btih:BBBB"),
    ]
    assert len(dedup_candidates(results)) == 2


def test_exclude_existing_drops_matching_infohash():
    results = [_result(fileUrl="magnet:?xt=urn:btih:AAAA")]
    assert exclude_existing(results, {"aaaa"}) == []


def test_exclude_existing_keeps_non_matching():
    results = [_result(fileUrl="magnet:?xt=urn:btih:AAAA")]
    assert exclude_existing(results, {"bbbb"}) == results


# ---------------------------------------------------------------------------
# Pass two: quality score — the Dune worked example from the project plan
# ---------------------------------------------------------------------------


def test_dune_worked_example_remux_hevc_mkv_wins():
    remux_webdl_mkv = _result(
        fileName="dune.2160p.remux.webdl.mkv", fileSize=40_000_000_000, fileUrl="magnet:?xt=urn:btih:1111"
    )
    remux_hevc_mkv = _result(
        fileName="dune.2160p.HEVC.remux.mkv", fileSize=85_000_000_000, fileUrl="magnet:?xt=urn:btih:2222"
    )
    h264_webdl_mp4 = _result(
        fileName="dune.2160p.h264.webdl.mp4", fileSize=34_000_000_000, fileUrl="magnet:?xt=urn:btih:3333"
    )

    ranked = rank_candidates([remux_webdl_mkv, remux_hevc_mkv, h264_webdl_mp4])
    winner, winner_score = ranked[0]

    assert winner["fileUrl"] == "magnet:?xt=urn:btih:2222"
    assert winner_score.source_score == 5  # remux
    assert winner_score.codec_score == 2  # hevc
    assert winner_score.container_score == 2  # mkv


def test_score_real_world_release_name_remux_outranks_bluray_token():
    # Real Stage 0 sample: both "BluRay" and "REMUX" tokens present — REMUX
    # is the more specific/higher tier and must win, not just "some source".
    score = score_candidate(
        _result(fileName="Dune.Part.Two.2024.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HEVC.REMUX-FraMeSToR")
    )
    assert score.source_score == 5
    assert score.codec_score == 2


def test_score_unknown_source_codec_container_score_zero():
    score = score_candidate(_result(fileName="Dune.Part.Two.2024.2160p.mkv"))
    assert score.resolution_score == 4
    assert score.source_score == 0
    assert score.codec_score == 0
    assert score.container_score == 2


def test_score_resolution_tiers():
    assert score_candidate(_result(fileName="Dune.2024.2160p.mkv")).resolution_score == 4
    assert score_candidate(_result(fileName="Dune.2024.4K.mkv")).resolution_score == 4
    assert score_candidate(_result(fileName="Dune.2024.1080p.mkv")).resolution_score == 3
    assert score_candidate(_result(fileName="Dune.2024.720p.mkv")).resolution_score == 2
    assert score_candidate(_result(fileName="Dune.2024.480p.mkv")).resolution_score == 1
    assert score_candidate(_result(fileName="Dune.2024.mkv")).resolution_score == 0


def test_rank_candidates_size_breaks_composite_tie():
    smaller = _result(fileName="Dune.2024.2160p.REMUX.mkv", fileSize=40_000_000_000, fileUrl="magnet:?xt=urn:btih:1")
    bigger = _result(fileName="Dune.2024.2160p.REMUX.mkv", fileSize=85_000_000_000, fileUrl="magnet:?xt=urn:btih:2")

    ranked = rank_candidates([smaller, bigger])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:2"


def test_rank_candidates_healthier_seeder_tier_outranks_unknown():
    unknown_seeders = _result(
        fileName="Dune.2024.2160p.REMUX.mkv", fileSize=40_000_000_000, nbSeeders=-1, fileUrl="magnet:?xt=urn:btih:1"
    )
    well_seeded = _result(
        fileName="Dune.2024.2160p.REMUX.mkv", fileSize=40_000_000_000, nbSeeders=50, fileUrl="magnet:?xt=urn:btih:2"
    )

    ranked = rank_candidates([unknown_seeders, well_seeded])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:2"


def test_rank_candidates_raw_seeders_break_tie_within_same_seeder_tier():
    fewer = _result(
        fileName="Dune.2024.2160p.REMUX.mkv", fileSize=40_000_000_000, nbSeeders=120, fileUrl="magnet:?xt=urn:btih:1"
    )
    more = _result(
        fileName="Dune.2024.2160p.REMUX.mkv", fileSize=40_000_000_000, nbSeeders=500, fileUrl="magnet:?xt=urn:btih:2"
    )

    ranked = rank_candidates([fewer, more])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:2"


def test_seeder_tiers():
    assert score_candidate(_result(nbSeeders=150)).seeder_score == 3
    assert score_candidate(_result(nbSeeders=30)).seeder_score == 2
    assert score_candidate(_result(nbSeeders=10)).seeder_score == 1
    assert score_candidate(_result(nbSeeders=9)).seeder_score == 0
    assert score_candidate(_result(nbSeeders=-1)).seeder_score == 1  # unknown: viable, not punished, but not rewarded either


def test_rank_candidates_seed_health_does_not_override_resolution_source_or_codec():
    # Seeder health outranks container (see below), but resolution, source,
    # and codec still can't be flipped by it — those are real quality
    # differences, container mostly isn't (see the next test).
    poorly_seeded_remux = _result(
        fileName="Dune.2024.2160p.REMUX.mkv", fileSize=40_000_000_000, nbSeeders=10, fileUrl="magnet:?xt=urn:btih:1"
    )
    well_seeded_webrip = _result(
        fileName="Dune.2024.2160p.WEBRip.mkv", fileSize=40_000_000_000, nbSeeders=5000, fileUrl="magnet:?xt=urn:btih:2"
    )

    ranked = rank_candidates([poorly_seeded_remux, well_seeded_webrip])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:1"


def test_rank_candidates_large_seeder_gap_overrides_container_difference():
    # Real-world case (Dune: Part Two, 2026-09-04): a manually-verified
    # 267-seeder REMUX with no stated container (near-certainly MKV by
    # convention — TrueHD/Atmos barely fits in MP4 anyway) was losing to a
    # 10-11 seeder release of the same source/codec tier purely because the
    # loser's name happened to say "MP4" explicitly. Container is a weak
    # enough signal that a large seeder-health gap should win this.
    unstated_container_well_seeded = _result(
        fileName="Dune.Part.Two.2024.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HEVC.REMUX-FraMeSToR",
        fileSize=69_026_891_608,
        nbSeeders=267,
        fileUrl="magnet:?xt=urn:btih:GOOD",
    )
    stated_mp4_poorly_seeded = _result(
        fileName="Dune.Part.Two.2024.2160p.BluRay.REMUX.DV.HDR.ENG.LATINO.DDP5.1.H265.MP4-BTM",
        fileSize=65_772_353_916,
        nbSeeders=10,
        fileUrl="magnet:?xt=urn:btih:BAD",
    )

    ranked = rank_candidates([stated_mp4_poorly_seeded, unstated_container_well_seeded])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:GOOD"


def test_sort_key_prefers_known_seeders_over_a_marginally_larger_unknown_file():
    # Real-world case, same incident: two near-identical-size postings of
    # the same release differed by <0.1% in size, and the larger one
    # happened to be the unknown-seeders posting — size shouldn't get to
    # decide ahead of "do we actually know this swarm is alive."
    known_smaller = _result(
        fileName="Dune.2024.2160p.REMUX.mkv", fileSize=65_772_353_916, nbSeeders=10, fileUrl="magnet:?xt=urn:btih:1"
    )
    unknown_larger = _result(
        fileName="Dune.2024.2160p.REMUX.mkv", fileSize=65_820_373_811, nbSeeders=-1, fileUrl="magnet:?xt=urn:btih:2"
    )

    ranked = rank_candidates([unknown_larger, known_smaller])
    assert ranked[0][0]["fileUrl"] == "magnet:?xt=urn:btih:1"
