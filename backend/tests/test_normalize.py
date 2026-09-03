from app.normalize import has_token, normalize_text, token_overlap, tokenize


def test_normalize_text_lowercases_and_strips_punctuation():
    assert normalize_text("Dune: Part Two") == "dune part two"


def test_normalize_text_strips_diacritics():
    assert normalize_text("Amélie") == "amelie"


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  Mission   Impossible  ") == "mission impossible"


def test_normalize_text_empty_string():
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_tokenize_splits_on_normalized_text():
    assert tokenize("Dune: Part Two") == ["dune", "part", "two"]


def test_has_token_whole_token_only_265_does_not_match_1265():
    assert has_token("Movie.1265.x264.mkv", "265") is False
    assert has_token("Movie.x265.mkv", "265") is False  # 'x265' is one token, not '265'
    assert has_token("Movie.265.mkv", "265") is True


def test_has_token_case_and_punctuation_insensitive():
    assert has_token("Movie.2160P.HEVC.mkv", "2160p") is True


def test_token_overlap():
    assert token_overlap("Dune Part Two", "dune.part.two.2024.2160p") == {"dune", "part", "two"}
