"""The named rows ask in the household's language.

The numbers in language.py's docstring are the reason this exists; these
are the rules that produce them.
"""

from app import language, moods


def test_a_mood_is_asked_in_the_households_language():
    params = language.with_preferred_language({"with_genres": 18}, "AU")

    assert params["with_original_language"] == "en"
    assert params["with_genres"] == 18


def test_the_language_follows_the_region_rather_than_being_english_everywhere():
    """The point is the household's language, not English — a French
    household asking for "Quietly Devastating" has the same complaint
    about a page of English films."""
    assert language.with_preferred_language({}, "FR")["with_original_language"] == "fr"
    assert language.with_preferred_language({}, "JP")["with_original_language"] == "ja"
    assert language.with_preferred_language({}, "de")["with_original_language"] == "de"


def test_a_row_that_names_its_own_language_keeps_it():
    """"Worth Reading the Subtitles" is Korean because its query says so.
    That is the whole exemption mechanism: no flag, no list of special
    keys — a row opts out by saying what it wants."""
    korean = {"with_original_language": "ko", "vote_count.gte": 200}

    assert language.with_preferred_language(korean, "AU") == korean


def test_the_subtitles_row_really_is_the_one_that_opts_out():
    """Pins the rule to the catalogue: if someone later drops the
    language from that mood's query, it quietly becomes an English row
    and the name stops being true."""
    opted_out = [m.key for m in moods.CATALOGUE if "with_original_language" in m.params]

    assert opted_out == ["mood:subtitles"]


def test_an_unknown_region_still_gets_a_preference():
    """No preference at all is the 66%-English page this exists to move,
    so an unlisted region falls back rather than opting out."""
    assert language.primary_language("XX") == language.DEFAULT_LANGUAGE
    assert language.primary_language(None) == language.DEFAULT_LANGUAGE


def test_the_original_query_is_not_mutated():
    """CATALOGUE is module-level and frozen per process — writing the
    language into a mood's own dict would pin the first household's
    region onto every later request."""
    original = {"with_genres": 18}

    language.with_preferred_language(original, "FR")

    assert original == {"with_genres": 18}


def test_every_offered_region_has_a_language():
    """The Settings picker's list and this table have to agree, or a
    household picks a country and silently gets English."""
    offered = {
        "AU", "BR", "CA", "DE", "ES", "FR", "GB", "IE", "IN",
        "IT", "JP", "MX", "NL", "NZ", "SE", "US", "ZA",
    }

    assert offered <= set(language.REGION_LANGUAGE)


# ---------------------------------------------------------------------------
# Audio track languages — what a container calls a language, and whether
# that is the one the household asked for.
# ---------------------------------------------------------------------------


def test_a_language_is_matched_whichever_spelling_the_container_used():
    """MKV usually carries ISO 639-2/B ("fre", "ger", "dut"), MP4 and
    some muxers carry 639-2/T ("fra", "deu", "nld"), and a few carry the
    two-letter code. Matching one spelling finds a French track in about
    half the files that have one."""
    assert language.is_audio_language("fre", "fr") is True
    assert language.is_audio_language("fra", "fr") is True
    assert language.is_audio_language("fr", "fr") is True
    assert language.is_audio_language("ger", "de") is True
    assert language.is_audio_language("deu", "de") is True
    assert language.is_audio_language("ENG", "en") is True


def test_an_untagged_track_is_never_the_preferred_one():
    """Guessing that an unlabelled track is the one someone wanted is how
    a household gets defaulted onto a commentary track or a dub — the
    exact class of problem this exists to stop."""
    for tag in (None, "", "und", "unk", "zxx", "mis"):
        assert language.is_audio_language(tag, "en") is False


def test_a_different_language_is_not_a_match():
    assert language.is_audio_language("jpn", "en") is False
    assert language.is_audio_language("spa", "fr") is False


def test_every_region_the_picker_offers_has_audio_aliases():
    """The Region card sets both fields, so a household can't be offered a
    country whose language this can't then find in a file."""
    for region, code in language.REGION_LANGUAGE.items():
        assert code in language.AUDIO_LANGUAGE_ALIASES, region
