"""Putting a newly filed title on the right audio track in Plex: which of
its tracks the household would choose, and selecting it on every server."""

import logging
import time
from dataclasses import dataclass

import requests

from app.catalogue.language import audio_quality_key, is_audio_language
from app.db import PlexServerRow
from app.plex.client import PlexClient, PlexError
from app.plex.library import locate_title

logger = logging.getLogger(__name__)


def _plex_audio_quality(stream: dict) -> tuple:
    """audio_quality_key over Plex's own field names."""
    return audio_quality_key(
        channels=stream.get("channels"),
        codec=stream.get("codec"),
        bitrate=(stream.get("bitrate") or 0) * 1000 or None,
        title=stream.get("extendedDisplayTitle") or stream.get("displayTitle") or stream.get("title"),
    )


@dataclass
class AudioSelection:
    """One part whose selected audio track isn't the one the household
    asked for."""

    title: str
    part_id: str
    stream_id: str
    from_language: str | None
    to_language: str
    languages: list[str]


def plan_audio_selection(server: PlexServerRow | None, client: "PlexClient", preferred: str) -> list[AudioSelection]:
    """Every part in Plex whose chosen audio track should change.

    Reads Plex rather than the filesystem on purpose: the question here
    is not what the file says but what Plex decided, and those two
    disagree for every file whose flag was corrected after Plex had
    already scanned it.

    Items with one audio track are skipped without a lookup, and an item
    already on the right language is left alone — the point is to change
    as little as possible.
    """
    if server is None:
        raise PlexError("no Plex server is linked yet")
    url, token = server.url, server.token

    out: list[AudioSelection] = []
    for section in client.sections(url, token):
        item_type = 1 if section["type"] == "movie" else 4 if section["type"] == "show" else None
        if item_type is None:
            continue
        for item in client.section_items(url, token, section["key"], item_type):
            detail = client.metadata(url, token, str(item.get("ratingKey")))
            if not detail:
                continue
            out += _selections_in(detail, preferred)
    return out


def _selections_in(detail: dict | None, preferred: str) -> list[AudioSelection]:
    """Every part of one Plex item whose chosen audio track should
    change. Shared so the library-wide sweep and the after-import nudge
    cannot drift apart about what "right" means."""
    if not detail:
        return []
    label = " - ".join(
        x for x in (detail.get("grandparentTitle"), detail.get("title")) if x
    ) or str(detail.get("ratingKey"))

    out: list[AudioSelection] = []
    for media in detail.get("Media", []) or []:
        for part in media.get("Part", []) or []:
            audio = [st for st in (part.get("Stream") or []) if st.get("streamType") == 2]
            if len(audio) < 2:
                continue
            # Plex reports ISO 639-2 ("eng", "ita") while the setting is
            # 639-1 ("en"), so these go through the same alias table the
            # organiser uses rather than a string compare, which matched
            # nothing at all.
            current = next((st for st in audio if st.get("selected")), None)
            candidates = [st for st in audio if is_audio_language(st.get("languageCode"), preferred)]
            if not candidates:
                continue
            # Best of them, not the first: a release routinely carries an
            # Atmos English track and a plain one, and a commentary is in
            # English too.
            wanted = max(candidates, key=_plex_audio_quality)
            if current is not None and current.get("id") == wanted.get("id"):
                continue
            out.append(
                AudioSelection(
                    title=label,
                    part_id=str(part.get("id")),
                    stream_id=str(wanted.get("id")),
                    from_language=(current or {}).get("languageCode"),
                    to_language=preferred,
                    languages=[(st.get("languageCode") or "?") for st in audio],
                )
            )
    return out


def select_audio_after_import(
    store,
    server: PlexServerRow | None,
    client: "PlexClient",
    media_type: str,
    title: str,
    year: int | None,
    tmdb_id: int | None,
    preferred: str,
    attempts: int = 4,
    delay: float = 15.0,
) -> int:
    """Tell Plex which audio track this title should use, once it has
    finished scanning it in.

    Setting the flag in the file decides Plex's *first* choice and
    nothing after it, and "first choice" turned out to be a weaker
    guarantee than it sounds. A viewer's own language preference sits
    above the flag, a remux that failed leaves the old flag in place,
    and Plex records whatever it picked and never revisits it. The
    household hit all of this on The Gorge: the file had English as its
    only default and Plex played the Russian dub regardless.

    So the file is told, and then Plex is told, and neither is trusted
    to have understood the other. Idempotent — an item already on the
    right track plans nothing.

    Retried because a scan takes as long as it takes: the refresh that
    precedes this only *asks* Plex to look, so the part may not exist
    for a few seconds yet. Best effort throughout; a failure here is
    logged and never raised into the worker, because a correct file
    filed in the right place is still a success.
    """
    if server is None:
        return 0

    for attempt in range(attempts):
        if attempt:
            time.sleep(delay)
        try:
            found = locate_title(store, server, client, media_type, title, year, tmdb_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("plex audio selection: couldn't locate %r yet (%s)", title, exc)
            continue
        if not found:
            continue
        try:
            detail = client.metadata(server.url, server.token, str(found["rating_key"]))
            changed = 0
            for choice in _selections_in(detail, preferred):
                if apply_audio_selection(server, client, choice) is None:
                    changed += 1
            if changed:
                logger.info(
                    "plex audio selection: put %d part(s) of %r on %s after import", changed, title, preferred
                )
            return changed
        except Exception as exc:  # noqa: BLE001
            logger.info("plex audio selection for %r failed: %s", title, exc)
            return 0
    logger.info("plex audio selection: %r hadn't appeared in Plex after %d tries", title, attempts)
    return 0


def apply_audio_selection(server: PlexServerRow, client: "PlexClient", choice: AudioSelection) -> str | None:
    """Makes one selection stick. None on success, the reason otherwise."""
    try:
        client.select_audio_stream(server.url, server.token, choice.part_id, choice.stream_id)
    except (PlexError, requests.RequestException) as exc:
        return str(exc)
    return None
