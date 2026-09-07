from app.normalize import has_token, normalize_text, titles_match, token_overlap, tokenize


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


def test_titles_match_exact():
    assert titles_match("the mandalorian and grogu", "the mandalorian and grogu") is True


def test_titles_match_franchise_prefix():
    """The real case this exists for: Plex's own scraped title carries a
    franchise prefix TMDB's plain title doesn't."""
    assert titles_match("star wars the mandalorian and grogu", "the mandalorian and grogu") is True
    assert titles_match("the mandalorian and grogu", "star wars the mandalorian and grogu") is True


def test_titles_match_subtitle_suffix():
    assert titles_match("the mandalorian and grogu extended cut", "the mandalorian and grogu") is True


def test_titles_match_allows_single_word_match():
    """At the user's explicit request: a one-word title/alias on the
    shorter side is allowed to match — a deliberately accepted
    false-positive tradeoff over ever missing a real match."""
    assert titles_match("grogu", "the mandalorian and grogu") is True


def test_titles_match_rejects_unrelated_titles():
    assert titles_match("dune part two", "the mandalorian and grogu") is False


def test_titles_match_rejects_partial_middle_overlap():
    """Shared words in the middle, not at a whole prefix/suffix boundary,
    must not match — this is containment, not fuzzy overlap."""
    assert titles_match("the mandalorian returns and grogu", "the mandalorian and grogu") is False
