"""Which language a household's named rows should be in.

Measured 2026-09-26 against the live catalogue: across all 45 named rows
in moods.py, 900 titles came back 66% English — and the shortfall was not
spread evenly. "Animated, and Not for the Kids" was 20% English (anime
dominates adult animation by rating), the TV rows ran 30-45%, and several
came back majority Korean or Japanese. The personalised rows have no such
problem: TMDB's /recommendations for an English seed measured 100%
English, so nothing here touches them.

That gap is a property of the query, not of anyone's taste. "Drama, rated
above 7.5, with a vote floor" is a world query, and the world does not
mostly make films in English. A household in Australia asking for
"Quietly Devastating" is not asking for a world survey.

So the named rows ask in the household's own language. Nothing is banned
anywhere else, and a row that names its own language keeps it — which is
what lets "Worth Reading the Subtitles" stay Korean while the rows around
it stop being.
"""

from __future__ import annotations

# The main production language of each region offered in Settings (see
# regions.ts). A country with more than one is given the one TMDB is most
# likely to have tagged: "en" for Canada, since TMDB records Quebecois
# cinema as "fr" but the bulk of what a Canadian household would recognise
# is tagged English, and "hi" for India, which is the largest single
# original_language among Indian titles rather than a claim about the
# country.
REGION_LANGUAGE: dict[str, str] = {
    "AU": "en",
    "BR": "pt",
    "CA": "en",
    "DE": "de",
    "ES": "es",
    "FR": "fr",
    "GB": "en",
    "IE": "en",
    "IN": "hi",
    "IT": "it",
    "JP": "ja",
    "MX": "es",
    "NL": "nl",
    "NZ": "en",
    "SE": "sv",
    "US": "en",
    "ZA": "en",
}

# For a region not in the table. English rather than nothing: an unlisted
# region is one the Settings picker never offered, so it arrived from a
# hand-edited setting, and the alternative — no preference at all — is the
# 66% this module exists to move.
DEFAULT_LANGUAGE = "en"


def primary_language(region: str | None) -> str:
    """The language a household in `region` should be offered first."""
    return REGION_LANGUAGE.get((region or "").upper(), DEFAULT_LANGUAGE)


def with_preferred_language(params: dict, region: str | None) -> dict:
    """A mood's TMDB query, asked in the household's language.

    A mood that already names a language is returned untouched. That is
    the whole exemption mechanism, and it is deliberately not a flag:
    "Worth Reading the Subtitles" is Korean *because its query says so*,
    so the query saying so is the honest place for the row to opt out.
    Any future row that wants to be foreign only has to say which.
    """
    if "with_original_language" in params:
        return params
    return {**params, "with_original_language": primary_language(region)}


# What a container calls a language, per ISO 639-1 code.
#
# ffprobe reports a track's language from the container's own metadata,
# and containers disagree: MKV usually carries ISO 639-2/B ("fre", "ger",
# "dut"), MP4 and some muxers carry 639-2/T ("fra", "deu", "nld"), and a
# few carry the two-letter code. Matching one spelling finds a French
# track in half the files that have one.
AUDIO_LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "ar": ("ar", "ara"),
    "da": ("da", "dan"),
    "de": ("de", "deu", "ger"),
    "en": ("en", "eng"),
    "es": ("es", "spa"),
    "fi": ("fi", "fin"),
    "fr": ("fr", "fra", "fre"),
    "hi": ("hi", "hin"),
    "it": ("it", "ita"),
    "ja": ("ja", "jpn"),
    "ko": ("ko", "kor"),
    "nl": ("nl", "nld", "dut"),
    "no": ("no", "nor"),
    "pl": ("pl", "pol"),
    "pt": ("pt", "por"),
    "ru": ("ru", "rus"),
    "sv": ("sv", "swe"),
    "tr": ("tr", "tur"),
    "zh": ("zh", "zho", "chi"),
}


def is_audio_language(tag: str | None, code: str) -> bool:
    """True when a track's own language tag names `code`.

    An untagged track ("und", or nothing at all) is never a match. That
    is deliberate rather than lenient: guessing that an unlabelled track
    is the preferred one is how a household ends up defaulted onto a
    commentary track or a foreign dub, and the whole point of this is to
    stop people landing on audio they didn't choose."""
    if not tag:
        return False
    tag = tag.strip().lower()
    if tag in ("und", "unk", "mis", "zxx"):
        return False
    return tag in AUDIO_LANGUAGE_ALIASES.get(code.lower(), (code.lower(),))
