"""Filing a season pack or a whole series: matching each of the release's
files to its episode, joining multi-part episodes into one file, and
hardlinking every episode into Plex's TV layout (filing.py's rules)."""

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from app.box import drives
from app.core import config
from app.library.containers import ffmpeg_reason, probe_duration_minutes
from app.library.filing import (
    MediaOrganizerError,
    NoVideoFileError,
    build_episode_path,
    find_release_dir,
    link_or_copy,
    translate_qbit_save_path,
)
from app.matching.normalize import extract_episode_identity, has_token, tokenize
from app.matching.tv_resolve import ShowIdentity
from app.qbittorrent.client import QBTClient

logger = logging.getLogger(__name__)


def _pack_entries(
    torrent_hash: str | None, qbt: QBTClient, release_name: str | None
) -> tuple[list[tuple[str, Path]], str]:
    """Every file in the pack as (name, absolute source path) pairs —
    from qBittorrent while it still holds the torrent, and from the disk
    once it doesn't.

    The fallback exists because the torrent is not reliably still there
    when this runs, or when it is re-run. qBittorrent can be set to drop
    a torrent shortly after it finishes seeding (ten minutes, on the
    deployment this was written for) while leaving the data in place,
    and an organize that fails for any reason parks its request on
    "downloaded, not filed" — a status the rest of the app treats as
    recoverable. It wasn't: the file list only ever came from the
    torrent, so once that went, every retry failed identically and the
    request was stranded for good, including ones a later fix would
    otherwise have cleared. The files are still on disk; read them from
    there.

    Returns the entries and a short description of where they came
    from, so the caller's errors can say which."""
    info = qbt.torrent_info(torrent_hash) if torrent_hash else None
    if info is not None:
        save_path = info.get("save_path")
        if not save_path:
            raise MediaOrganizerError(f"torrent {torrent_hash!r} has no save_path")
        base = translate_qbit_save_path(save_path, config.QBIT_TV_SAVE_PATH, config.TV_LIBRARY_ROOT)
        files = qbt.torrent_files(torrent_hash)
        return [(f["name"], base / f["name"]) for f in files], f"torrent {torrent_hash!r}"

    if not release_name:
        raise MediaOrganizerError(
            f"torrent {torrent_hash!r} not found in qBittorrent, and no release name to find it on disk with"
        )
    # Where qBittorrent saved TV: its own setting's folder, or on a box each
    # drive's TV downloads.
    searched = (
        [d.downloads / config.TV_CATEGORY for d in drives.all_drives()] if drives.managed() else [config.TV_LIBRARY_ROOT]
    )
    release_dir = next((found for root in searched if (found := find_release_dir(root, release_name))), None)
    if release_dir is None:
        raise MediaOrganizerError(
            f"torrent {torrent_hash!r} not found in qBittorrent, and no folder matching "
            f"{release_name!r} under {', '.join(str(r) for r in searched) or 'any drive'}"
        )
    logger.info(
        "organize_pack: torrent %r is gone from qBittorrent — reading %s off disk instead",
        torrent_hash,
        release_dir,
    )
    entries = [
        (str(path.relative_to(release_dir.parent)), path)
        for path in sorted(release_dir.rglob("*"))
        if path.is_file()
    ]
    return entries, f"folder {str(release_dir)!r}"


def concat_parts(sources: list[Path], target: Path) -> bool:
    """Stitches a release's parts into the one episode TMDB says they
    are. Stream copy — nothing is re-encoded, and nothing is decoded.

    The parts come out of one release, so they share codec, resolution
    and channel layout, which is what makes `-c copy` safe here and
    would not make it safe in general.

    Verified by duration before anything replaces anything: concat can
    exit clean having written a short file if a part is unreadable, and
    the result of getting that wrong is an episode that looks filed and
    plays half of itself. A result more than five seconds short of the
    parts it came from is thrown away.

    Returns False rather than raising, so a pack whose parts won't
    stitch falls back to filing them as they are.
    """
    durations = [probe_duration_minutes(part) for part in sources]
    expected = sum(d for d in durations if d) if all(durations) else None

    listing = target.with_name(f".{target.stem}.concat.txt")
    tmp = target.with_name(f".{target.stem}.joining.tmp{target.suffix}")
    try:
        # ffmpeg's concat list quotes with single quotes and escapes them
        # the awkward way; a title with an apostrophe in it is not rare.
        listing.write_text(
            "".join(f"file '{str(part).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for part in sources),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-map", "0", "-map", "-0:d?", "-c", "copy", str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=config.FFMPEG_STRIP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not join %d part(s) into %r (%s)", len(sources), target.name, exc)
        listing.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        return False
    finally:
        listing.unlink(missing_ok=True)

    if result.returncode != 0:
        logger.warning("ffmpeg could not join %d part(s) into %r: %s",
                       len(sources), target.name, ffmpeg_reason(result.stderr))
        tmp.unlink(missing_ok=True)
        return False

    joined = probe_duration_minutes(tmp)
    if expected and (joined is None or joined < expected - (5 / 60)):
        logger.warning(
            "joined %d part(s) into %r but it came out %.1f min against the %.1f min they run to — discarding it",
            len(sources), target.name, joined or 0, expected,
        )
        tmp.unlink(missing_ok=True)
        return False

    os.replace(tmp, target)
    logger.info("joined %d part(s) into %r (%.1f min)", len(sources), target.name, joined or 0)
    return True


def _pack_file_episode(name: str) -> tuple[int, int] | None:
    """Which (season, episode) one file in a pack is, or None when it is
    not an episode at all — not a video, a sample, or no episode token.

    The one reading of a pack's files, shared by pack_season_span (how
    many seasons does this pack hold?) and organize_pack (where does each
    file go?). They used to read names differently: the span looked at
    the whole path with the plain parser, the organizer at the file's own
    name with the "1x01" form allowed too. So a pack whose files the
    organizer could place, the span could miss — and a Justice League
    Unlimited pack whose first two seasons it didn't count looked like
    seasons 3-5 of one show, skipped the check that splits off the show
    bundled in front, and was filed as Unlimited from end to end
    (2026-09-29). One function, so the two can't disagree again."""
    path = Path(name)
    if path.suffix.lower() not in config.VIDEO_EXTENSIONS or has_token(path.stem, "sample"):
        return None
    return extract_episode_identity(tokenize(path.stem), allow_x_form=True)


def pack_season_span(
    torrent_hash: str | None, qbt: QBTClient, release_name: str | None = None
) -> tuple[int, int] | None:
    """The lowest and highest season numbered inside a pack, read from
    the files themselves rather than from the release's name.

    The name is a claim and the files are the fact, and for the decision
    this feeds — whether a pack is carrying more than one show — only
    the fact will do. Read exactly as organize_pack reads them (see
    _pack_file_episode). Season 0 is ignored: a bundled specials folder
    says nothing about how many series are in here.
    """
    entries, _ = _pack_entries(torrent_hash, qbt, release_name)
    seasons = set()
    for name, _path in entries:
        identity = _pack_file_episode(name)
        if identity and identity[0] > 0:
            seasons.add(identity[0])
    return (min(seasons), max(seasons)) if seasons else None


def organize_pack(
    show_identity: ShowIdentity,
    torrent_hash: str | None,
    qbt: QBTClient,
    release_name: str | None = None,
    place: Callable[[int, int], tuple[int, int, str | None]] | None = None,
    preferred_audio_language: str | None = None,
    only_seasons: tuple[int, int] | None = None,
    season_offset: int = 0,
    align: Callable[[int, list[int]], dict[int, list[int]] | None] | None = None,
) -> list[tuple[int, int, Path]]:
    """Stage 13: places every individually SxxEyy-identifiable file out of a
    completed season/complete-series pack torrent — an extension of
    `organize_episode`'s own per-file placement (`build_episode_path` +
    hardlink-or-copy), reused here unchanged for each file rather than
    reimplemented, per the plan's "reuse, don't duplicate" call. The only
    genuinely new logic is per-file: which (season, episode) a given file
    represents (`normalize.extract_episode_identity`, read from the file's
    own name, not from a single fixed request the way `organize_episode`
    is told its season/episode).

    A file with no recognizable episode token — a sample, .nfo, subtitle
    sidecar, or any other unmatched extra — is skipped and logged, never
    treated as an error: a pack's real content is "however many episodes
    it actually turns out to carry," not a fixed count this function
    checks against (Stage 13's "accept what's actually there" resolution
    of its own "partial pack" open decision). Raises `MediaOrganizerError`
    if neither the torrent nor, failing that, a folder on disk matching
    `release_name` can be found (see `_pack_entries`), or the more
    specific `NoVideoFileError`
    if literally nothing in it is recognizable as an episode — worker.py
    treats that specific case as grounds to purge the torrent outright,
    same as `select_video_file`'s own single-episode equivalent.

    Returns one `(season, episode, target_path)` tuple per file actually
    placed — the season and episode being where the file *went*, which
    `place` may have moved (a special appended to a season lands in
    Season 00), so the caller's ledger records where it is rather than
    what the release called it; the caller (worker.py) is what maps this back onto per-episode
    `requests` rows and Stage 12's `show_episodes` dedup ledger — this
    function only ever touches the filesystem, never the database."""
    entries, source_desc = _pack_entries(torrent_hash, qbt, release_name)
    placed: list[tuple[int, int, Path]] = []
    by_season: dict[int, list[tuple[int, Path, Path]]] = {}
    any_video_file = False
    for name, source_path in entries:
        path = Path(name)
        if path.suffix.lower() not in config.VIDEO_EXTENSIONS or has_token(path.stem, "sample"):
            continue
        any_video_file = True
        identity_pair = _pack_file_episode(name)
        if identity_pair is None:
            logger.info("organize_pack: skipping %r — no recognizable episode token", name)
            continue
        season, episode = identity_pair
        # A callable rather than prepared data: which seasons a pack
        # actually carries is only known here, file by file — a complete-
        # series pack would otherwise have to be pre-fetched season by
        # season on the chance it needed them. The caller memoises.
        #
        # It can move a file as well as name one. A pack that appends a
        # special to the end of a season (Invincible's Atom Eve ships as
        # S01E09, where TMDB has S00E01 and season 1 stops at 8) is
        # re-placed into Season 00 — see tv_resolve.episode_placement_lookup,
        # which owns that judgement and the TMDB data behind it.
        # A franchise pack holds more than one show: "Justice League
        # Unlimited S01-S05" is Justice League's two seasons followed by
        # Unlimited's three. The caller files it in two passes, each
        # claiming its own slice and renumbering it onto that show's own
        # seasons — see tv_resolve.find_predecessor_show.
        if only_seasons and not (only_seasons[0] <= season <= only_seasons[1]):
            continue
        season += season_offset
        by_season.setdefault(season, []).append((episode, path, source_path))

    # A release sometimes ships as several files what TMDB records as one
    # episode — Justice League's pilot is three broadcast parts against a
    # single 72-minute episode 1 — and every file after them is then
    # numbered ahead of the episode it claims to be. `align` says which
    # files make up which episode; see tv_resolve.align_pack_episodes,
    # which owns that judgement and refuses unless the arithmetic
    # balances.
    for season in sorted(by_season):
        files = sorted(by_season[season], key=lambda f: f[0])
        plan = align(season, [episode for episode, _p, _s in files]) if align else None
        if plan:
            by_number = {episode: entry for entry, episode in ((f, f[0]) for f in files)}
            grouped = [
                (target, [by_number[n] for n in sources if n in by_number])
                for target, sources in sorted(plan.items())
            ]
        else:
            grouped = [(entry[0], [entry]) for entry in files]
        for episode, members in grouped:
            if not members:
                continue
            _number, path, source_path = members[0]
            title: str | None = None
            if place:
                # The stem and a *lazy* duration: place() only reaches
                # for the runtime when the filename couldn't settle
                # which special a file is, so the extra ffprobe is paid
                # on the odd out-of-range file rather than on all twenty
                # in a pack.
                placed_season, episode, title = place(
                    season, episode, path.stem, lambda p=source_path: probe_duration_minutes(p)
                )
            else:
                placed_season = season
            target = build_episode_path(
                show_identity, placed_season, episode, source_path.suffix, title,
                drives.library_root("tv", near=source_path),
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            if len(members) > 1 and concat_parts([m[2] for m in members], target):
                placed.append((placed_season, episode, target))
                continue
            placed.append(
                (placed_season, episode, link_or_copy(source_path, target, preferred_audio_language))
            )

    if not placed:
        if not any_video_file:
            raise NoVideoFileError(
                f"no video file at all among {len(entries)} file(s) in {source_desc}"
            )
        # Real video file(s) present, just none this app's naming parser
        # could pin to a season/episode — a genuine "can't file this yet"
        # case (unusual release naming), not a fake-release signal, so it
        # stays recoverable ("downloaded, not filed") rather than purged.
        raise MediaOrganizerError(
            f"no recognizable episode files found among {len(entries)} file(s) in {source_desc}"
        )
    return placed
