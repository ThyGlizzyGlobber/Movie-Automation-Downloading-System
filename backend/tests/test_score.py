from app import config
from app.pipeline_settings import PipelineSettings
from app.resolve import MediaIdentity
from app.normalize import tokenize
from app.score import (
    passes_resolution_floor,
    dedup_candidates,
    exclude_existing,
    is_trustworthy,
    passes_language_filter,
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


def test_is_trustworthy_rejects_descrlink_only_result():
    """A real, live-caught case (Stage 12's Lanterns S01E03 validation):
    limetorrents' plugin hands back its own details webpage as `fileUrl`,
    byte-identical to `descrLink` — not a magnet or a real .torrent link.
    qBittorrent's add doesn't raise on this; it just fetches HTML, fails to
    parse it as a torrent, and the add silently never indexes. This was a
    named Stage 2 deliverable ("skip descrLink-only results") that had
    never actually been implemented until this was caught live."""
    url = "https://www.limetorrents.lol/Some-Torrent-12345.html"
    assert is_trustworthy(_result(fileUrl=url, descrLink=url)) is False


def test_is_trustworthy_accepts_a_real_fileurl_that_merely_shares_a_descrlink_field():
    """`descrLink` present but genuinely different from `fileUrl` is the
    normal, healthy shape (a details page alongside a real download link)
    — must not be rejected."""
    assert (
        is_trustworthy(
            _result(
                fileUrl="https://torlock.com/file/12345/movie.torrent",
                descrLink="https://torlock.com/torrent/12345/movie.html",
            )
        )
        is True
    )


def test_is_trustworthy_accepts_magnet_even_with_a_matching_descrlink():
    """A magnet is trusted unconditionally before the descrLink comparison
    even runs — some plugins duplicate the magnet into both fields."""
    magnet = "magnet:?xt=urn:btih:AAAA"
    assert is_trustworthy(_result(fileUrl=magnet, descrLink=magnet)) is True


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


def test_passes_relevance_gate_rejects_1080p_below_a_raised_household_floor(monkeypatch):
    monkeypatch.setattr(config, "MIN_RESOLUTION", "2160p")
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
# Archive-file exclusion
# ---------------------------------------------------------------------------


def test_passes_relevance_gate_rejects_zipx():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.zipx", DUNE) is False


def test_passes_relevance_gate_rejects_zip():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.zip", DUNE) is False


def test_passes_relevance_gate_rejects_rar():
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.rar", DUNE) is False


def test_passes_relevance_gate_rejects_exe():
    # Fake-release executable disguised as a video download — confirmed
    # live (a "Ted.Lasso...H264-NTb.exe" result on a real subscription).
    assert passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.exe", DUNE) is False


def test_passes_relevance_gate_rejects_tv_episode_of_a_same_titled_show():
    # Confirmed live 2026-09-14: a movie search for "Mayday" (2026) turned
    # up an episode of an unrelated, long-running documentary series of
    # the exact same title — matches_any_variant alone can't tell them
    # apart, since the episode's own release name carries "Mayday" as a
    # whole token same as the real movie would.
    mayday = MediaIdentity(
        tmdb_id=1137844, title="Mayday", original_title="Mayday", release_year=2026, variants=["Mayday"]
    )
    assert passes_relevance_gate("Mayday.S26E04.Crash.Landing.2160p.WEB-DL.mkv", mayday) is False
    assert passes_relevance_gate("Mayday.S26.E04.Crash.Landing.2160p.WEB-DL.mkv", mayday) is False


def test_passes_relevance_gate_rejects_tv_season_pack_of_a_same_titled_show():
    mayday = MediaIdentity(
        tmdb_id=1137844, title="Mayday", original_title="Mayday", release_year=2026, variants=["Mayday"]
    )
    assert passes_relevance_gate("Mayday.S26.COMPLETE.2160p.WEB-DL.mkv", mayday) is False


def test_passes_relevance_gate_still_accepts_a_real_movie_release():
    # The new filter shouldn't collide with ordinary movie naming — no
    # release ever legitimately carries a standalone "s01"-style token.
    assert passes_relevance_gate("Mayday.2026.2160p.WEB-DL.mkv", MediaIdentity(
        tmdb_id=1137844, title="Mayday", original_title="Mayday", release_year=2026, variants=["Mayday"]
    )) is True


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
# Stage 7: an explicit PipelineSettings object — the real runtime path
# (worker.py resolves this from the Settings-panel-backed store), as
# opposed to monkeypatching config.py above.
# ---------------------------------------------------------------------------

_PERMISSIVE_SETTINGS = PipelineSettings(
    category="movies",
    min_resolution="1080p",
    min_size_gb=1,
    max_size_gb=150,
    language_allowlist=(),
    language_blocklist=(),
)


def test_passes_relevance_gate_with_explicit_settings_lowered_floor():
    assert passes_relevance_gate("Dune.Part.Two.2024.1080p.WEBRip.mkv", DUNE, _PERMISSIVE_SETTINGS) is True


def test_passes_relevance_gate_with_explicit_settings_still_rejects_below_floor():
    assert passes_relevance_gate("Dune.Part.Two.2024.720p.WEBRip.mkv", DUNE, _PERMISSIVE_SETTINGS) is False


def test_passes_relevance_gate_with_explicit_settings_blocklist():
    blocklist_settings = PipelineSettings(
        category="movies",
        min_resolution="2160p",
        min_size_gb=1,
        max_size_gb=150,
        language_allowlist=(),
        language_blocklist=("french",),
    )
    assert (
        passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.FRENCH.mkv", DUNE, blocklist_settings) is False
    )


def test_passes_relevance_gate_with_explicit_settings_allowlist_excludes_unlisted_language():
    allowlist_settings = PipelineSettings(
        category="movies",
        min_resolution="2160p",
        min_size_gb=1,
        max_size_gb=150,
        language_allowlist=("italian",),
        language_blocklist=(),
    )
    assert (
        passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.ENGLISH.mkv", DUNE, allowlist_settings) is False
    )


def test_passes_relevance_gate_with_explicit_settings_required_needs_every_language():
    # AND semantics, distinct from allowlist's OR: a dual-audio release
    # needs English *and* Spanish together, not just one of the two.
    required_settings = PipelineSettings(
        category="movies",
        min_resolution="2160p",
        min_size_gb=1,
        max_size_gb=150,
        language_allowlist=(),
        language_blocklist=(),
        language_required=("english", "spanish"),
    )
    assert (
        passes_relevance_gate("Dune.Part.Two.2024.2160p.REMUX.ENGLISH.mkv", DUNE, required_settings) is False
    )
    assert (
        passes_relevance_gate(
            "Dune.Part.Two.2024.2160p.REMUX.ENGLISH.SPANISH.mkv", DUNE, required_settings
        )
        is True
    )


# ---------------------------------------------------------------------------
# passes_language_filter directly — required (AND) is independent of, and
# stricter than, allowlist (OR).
# ---------------------------------------------------------------------------


def test_passes_language_filter_required_rejects_when_only_one_of_several_present():
    tokens = tokenize("Movie.2024.ENGLISH.mkv")
    assert passes_language_filter(tokens, (), (), required=("english", "spanish")) is False


def test_passes_language_filter_required_accepts_when_every_language_present():
    tokens = tokenize("Movie.2024.ENGLISH.SPANISH.mkv")
    assert passes_language_filter(tokens, (), (), required=("english", "spanish")) is True


def test_passes_language_filter_required_still_respects_blocklist():
    tokens = tokenize("Movie.2024.ENGLISH.SPANISH.CAM.mkv")
    assert passes_language_filter(tokens, (), ("cam",), required=("english", "spanish")) is False


def test_passes_language_filter_empty_required_is_a_no_op():
    tokens = tokenize("Movie.2024.mkv")
    assert passes_language_filter(tokens, (), (), required=()) is True


def test_passes_viability_gate_with_explicit_settings_size_range():
    tight_settings = PipelineSettings(
        category="movies",
        min_resolution="2160p",
        min_size_gb=50,
        max_size_gb=60,
        language_allowlist=(),
        language_blocklist=(),
    )
    assert passes_viability_gate(_result(fileSize=40_000_000_000), tight_settings) is False
    assert passes_viability_gate(_result(fileSize=55_000_000_000), tight_settings) is True


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


# -- Partial title variants only match when release metadata follows --


def test_partial_variant_rejects_a_longer_title_that_shares_the_head():
    from app.normalize import generate_variants, tokenize
    from app.score import matches_any_variant

    variants = generate_variants("Batman: The Brave and the Bold", "Batman: The Brave and the Bold", None)
    assert "Batman" in variants
    assert not matches_any_variant(tokenize("Batman.Beyond.S01E01.1080p.WEB-DL.x264"), variants)
    assert not matches_any_variant(tokenize("Batman.The.Animated.Series.Complete.1080p"), variants)
    assert matches_any_variant(tokenize("Batman.S01E01.1080p.WEB-DL"), variants)
    assert matches_any_variant(tokenize("Batman.The.Brave.and.the.Bold.S01E03.720p"), variants)
    assert matches_any_variant(tokenize("Batman.The.Brave.And.The.Bold.Complete.Series.1080p"), variants)


def test_partial_subtitle_variant_still_matches_when_metadata_follows():
    from app.normalize import generate_variants, tokenize
    from app.score import matches_any_variant

    variants = generate_variants("Special Ops: Lioness", "Special Ops: Lioness", None)
    assert matches_any_variant(tokenize("Lioness.S01E01.2160p.WEB.H265"), variants)
    assert matches_any_variant(tokenize("Special.Ops.Lioness.S02E04.1080p"), variants)
    assert not matches_any_variant(tokenize("Lioness.Cubs.S01E01.1080p"), variants)


def test_partial_variant_for_a_movie_needs_the_year_or_quality_right_after():
    from app.normalize import generate_variants, tokenize
    from app.score import matches_any_variant

    variants = generate_variants("Dune: Part Two", "Dune: Part Two", 2024)
    assert matches_any_variant(tokenize("Dune.Part.Two.2024.2160p.WEB-DL"), variants)
    assert not matches_any_variant(tokenize("Dune.Prophecy.S01E01.1080p"), variants)
    assert matches_any_variant(tokenize("Dune.2024.1080p"), variants)


def test_full_title_and_original_title_still_match_anywhere():
    from app.normalize import tokenize
    from app.score import matches_any_variant

    variants = ["Cobalt Hour", "L'Heure Cobalt"]
    assert matches_any_variant(tokenize("1080p.Cobalt.Hour.2024"), variants)
    assert matches_any_variant(tokenize("L.Heure.Cobalt.2024.FRENCH.1080p"), variants)
    assert not matches_any_variant(tokenize("Golden.Hour.2024.1080p"), variants)
    # Another word in front of the title is more title, not this one.
    assert not matches_any_variant(tokenize("After.Cobalt.Hour.2024.1080p"), variants)


def test_a_title_without_a_subtitle_is_its_own_full_title_and_matches_anywhere():
    from app.normalize import generate_variants, tokenize
    from app.score import matches_any_variant

    variants = generate_variants("Batman Beyond", "Batman Beyond", None)
    assert variants == ["Batman Beyond"]
    assert matches_any_variant(tokenize("Batman.Beyond.S01E01.2160p.WEB-DL"), variants)
    assert matches_any_variant(tokenize("Batman.Beyond.Complete.Series.1080p.BluRay"), variants)
    assert not matches_any_variant(tokenize("Batman.The.Brave.and.the.Bold.S01E01.2160p"), variants)
    assert not matches_any_variant(tokenize("Batman.S01E01.2160p"), variants)


# ---------------------------------------------------------------------------
# SD rips name their source instead of a resolution ("TVRip", "DVDRip",
# "HDTV XviD") — those count as the 480p tier so an "Anything" floor really
# accepts them, while any explicit higher token in the name still wins.
# ---------------------------------------------------------------------------


def test_sd_source_words_count_as_the_lowest_tier():
    for name in ("Show.S01.TVRip.x264", "Show.2008.DVDRip.XviD", "Show.S01E01.HDTV.XviD-FQM", "Show.S01.PDTV"):
        tokens = tokenize(name)
        assert passes_resolution_floor(tokens, "480p") is True, name
        assert passes_resolution_floor(tokens, "720p") is False, name


def test_an_explicit_resolution_still_outranks_an_sd_source_word():
    assert score_candidate(_result(fileName="Show.S01E01.720p.HDTV.x264.mkv")).resolution_score == 2
    assert score_candidate(_result(fileName="Show.S01E01.HDTV.XviD.avi")).resolution_score == 1


def test_no_source_or_resolution_word_still_fails_every_floor():
    assert passes_resolution_floor(tokenize("Batman - The Brave And The Bold - Complete"), "480p") is False


# A movie title is only a match when release metadata follows it.
def test_relevance_gate_rejects_a_sequel_or_longer_title_for_a_short_one():
    ted = MediaIdentity(tmdb_id=1, title="Ted", original_title="Ted", release_year=2012, variants=["Ted", "Ted 2012"])
    assert passes_relevance_gate("Ted.2.2015.2160p.BluRay.mkv", ted) is False
    assert passes_relevance_gate("Ted.2012.UNRATED.2160p.BluRay.mkv", ted) is True
    assert passes_relevance_gate("Ted.UNRATED.2012.2160p.BluRay.mkv", ted) is True
