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


def test_a_film_of_a_show_is_not_split_into_the_shows_name():
    """"Batman Beyond: The Movie" split like any subtitle gave "Batman
    Beyond" — the series' own name — and every series pack passed as the
    film (2026-09-29)."""
    assert generate_variants("Batman Beyond: The Movie", "Batman Beyond: The Movie", 1999) == [
        "Batman Beyond: The Movie",
        "Batman Beyond: The Movie 1999",
    ]
    assert "Star Trek" not in generate_variants("Star Trek: The Motion Picture", "Star Trek: The Motion Picture", 1979)


def test_an_ordinary_subtitle_is_still_split():
    assert "Dune" in generate_variants("Dune: Part Two", "Dune: Part Two", 2024)


def test_an_apostrophe_is_also_searched_dropped():
    """Wonka's The Golden Ticket is Wonkas.The.Golden.Ticket on every
    tracker; normalizing the apostrophe to a space alone ("wonka s")
    matched none of them."""
    variants = generate_variants("Wonka's The Golden Ticket", "Wonka's The Golden Ticket", None)
    assert variants == ["Wonka's The Golden Ticket", "Wonkas The Golden Ticket"]


def test_a_curly_apostrophe_is_dropped_too():
    variants = generate_variants("Ocean’s Eleven", "Ocean’s Eleven", 2001)
    assert variants[1] == "Oceans Eleven"


def test_a_release_without_the_apostrophe_now_matches():
    from app.normalize import tokenize
    from app.score import matches_any_variant

    variants = generate_variants("Wonka's The Golden Ticket", "Wonka's The Golden Ticket", None)
    release = tokenize("Wonkas.The.Golden.Ticket.S01.1080p.NF.WEB-DL.DDP5.1.H.264-FLUX")
    assert matches_any_variant(release, variants)
    assert matches_any_variant(tokenize("Wonka.s.The.Golden.Ticket.S01E01.720p"), variants)
