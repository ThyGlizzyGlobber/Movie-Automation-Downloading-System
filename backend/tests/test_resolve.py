from app.resolve import generate_variants


def test_generate_variants_franchise_title_with_subtitle():
    variants = generate_variants("Dune: Part Two", "Dune: Part Two", 2024)
    assert variants == ["Dune: Part Two", "Dune", "Dune: Part Two 2024"]


def test_generate_variants_dedupes_when_original_title_matches():
    variants = generate_variants("John Wick", "John Wick", 2014)
    assert variants == ["John Wick", "John Wick 2014"]


def test_generate_variants_includes_original_title_when_different():
    variants = generate_variants("Spirited Away", "千と千尋の神隠し", 2001)
    assert variants[:2] == ["Spirited Away", "千と千尋の神隠し"]


def test_generate_variants_no_subtitle_no_duplicate_title_only_entry():
    variants = generate_variants("Oppenheimer", "Oppenheimer", 2023)
    assert variants == ["Oppenheimer", "Oppenheimer 2023"]


def test_generate_variants_caps_at_four():
    variants = generate_variants("Mission: Impossible - Dead Reckoning", "Mission: Impossible - Dead Reckoning", 2023)
    assert len(variants) <= 4


def test_generate_variants_no_year_still_works():
    variants = generate_variants("Dune: Part Two", "Dune: Part Two", None)
    assert variants == ["Dune: Part Two", "Dune"]
