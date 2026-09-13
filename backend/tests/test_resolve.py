from app.resolve import generate_variants


def test_generate_variants_franchise_title_with_subtitle():
    variants = generate_variants("Dune: Part Two", "Dune: Part Two", 2024)
    assert variants == ["Dune: Part Two", "Dune", "Part Two", "Dune: Part Two 2024"]


def test_generate_variants_dedupes_when_original_title_matches():
    variants = generate_variants("John Wick", "John Wick", 2014)
    assert variants == ["John Wick", "John Wick 2014"]


def test_generate_variants_includes_original_title_when_different():
    variants = generate_variants("Spirited Away", "千と千尋の神隠し", 2001)
    assert variants[:2] == ["Spirited Away", "千と千尋の神隠し"]


def test_generate_variants_no_subtitle_no_duplicate_title_only_entry():
    variants = generate_variants("Oppenheimer", "Oppenheimer", 2023)
    assert variants == ["Oppenheimer", "Oppenheimer 2023"]


def test_generate_variants_caps_at_max_variants():
    # title, original_title (different), head, tail, year — the true
    # maximum this generator can ever produce, one more than before the
    # tail-side variant was added (MAX_VARIANTS bumped 4 -> 5 to match).
    variants = generate_variants("Mission: Impossible - Dead Reckoning", "M:I Dead Reckoning", 2023)
    assert len(variants) == 5


def test_generate_variants_no_year_still_works():
    variants = generate_variants("Dune: Part Two", "Dune: Part Two", None)
    assert variants == ["Dune: Part Two", "Dune", "Part Two"]


def test_generate_variants_includes_the_tail_side_of_a_subtitle_split():
    """The whole reason both sides are generated now, not just the head:
    confirmed live 2026-09-14, a movie/show search using only the head of
    a "Prefix: DistinctiveName"-shaped title ("Special Ops: Lioness")
    never tried "Lioness" — the actually distinctive, searchable part —
    at all, missing a real 2160p release a plain "Lioness" search found
    easily."""
    variants = generate_variants("Special Ops: Lioness", "Special Ops: Lioness", None)
    assert "Lioness" in variants
    assert "Special Ops" in variants
