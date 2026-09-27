import argparse
import asyncio
import sys
import time
from pathlib import Path

from app import config
from app.config import QBIT_HOST, QBIT_PASSWORD, QBIT_PORT, QBIT_USERNAME, TMDB_API_KEY
from app.db import RequestStore
from app.media_organizer import (
    MediaOrganizerError,
    apply_audio_default_fix,
    apply_container_conversion,
    organize_pack,
    plan_audio_default_fix,
    plan_container_conversion,
)
from app.pipeline_settings import resolve_pipeline_settings
from app.plex import PlexClient, PlexError, apply_audio_selection, plan_audio_selection
from app.qbt import QBTClient
from app.reconcile import remove_orphaned_download_dirs, remove_redundant_sources
from app.tmdb import TMDBClient
from app.tv_resolve import resolve_show
from app.worker import Worker


def cmd_organize_pack(args: argparse.Namespace) -> int:
    """Place every recognizable episode file from a real, already-completed
    pack torrent, by its hash.

    `--release-name` is the recovery path for a pack whose torrent
    qBittorrent has already dropped: with it, organize_pack finds the
    download's folder on disk by name instead. Note this command only
    touches the filesystem — see `retry-parked-packs` for the one that
    also settles the request rows."""
    tmdb_client = TMDBClient(TMDB_API_KEY)
    qbt = QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD)
    identity = resolve_show(args.tmdb_id, tmdb_client)

    try:
        placed = organize_pack(identity, args.torrent_hash, qbt, args.release_name)
    except MediaOrganizerError as exc:
        print(f"status:   downloaded, not filed ({exc})")
        return 1

    print(f"show:     {identity.title}")
    print(f"organized {len(placed)} episode(s):")
    for season, episode, target_path in placed:
        print(f"  S{season:02d}E{episode:02d} -> {target_path}")
    return 0


def cmd_retry_parked_packs(args: argparse.Namespace) -> int:
    """Re-run the organize step for every pack sitting on "downloaded,
    not filed", and settle its request rows if it works this time.

    That status means the download succeeded and the filing didn't, and
    nothing in the app retries it: the per-episode recheck loop walks
    `show_episodes`, and a pack only earns rows there once it has
    organized successfully. So a pack that failed once stayed failed,
    even after the reason was fixed. This is the way to pick those back
    up — run it after deploying a fix for whatever the error_message on
    those rows says.

    It drives `Worker._organize_and_complete_pack` rather than
    reimplementing it, so a retry does exactly what the original attempt
    would have: organize, fan out one requests row and one ledger entry
    per episode actually present, mark the pack organized, schedule the
    source cleanup, refresh Plex. Reorganizing is idempotent — an
    already-placed episode is replaced, not duplicated (`_link_or_copy`)
    and one already in the ledger is left alone — so running this twice
    is safe. The pack's own row is only moved off "downloaded, not
    filed" by the retry succeeding; a still-failing one keeps its status
    and gets a fresh error_message."""
    store = RequestStore(config.DB_PATH)
    rows = [r for r in store.list_requests(status="downloaded, not filed") if r.media_type == "pack"]
    if not rows:
        print("no packs are parked on 'downloaded, not filed'")
        return 0

    print(f"{len(rows)} parked pack(s):")
    for row in rows:
        print(f"  #{row.id} {row.title} S{row.season_number} — {row.error_message}")
    if not args.apply:
        print("\ndry run; pass --apply to retry them")
        return 0

    worker = Worker(store, TMDBClient(TMDB_API_KEY), QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD))
    failed = 0
    for row in rows:
        print(f"\nretrying #{row.id} {row.title} S{row.season_number}...")
        asyncio.run(worker._organize_and_complete_pack(row))
        after = store.get_request(row.id)
        print(f"  -> {after.status}" + (f" ({after.error_message})" if after.error_message else ""))
        if after.status != "complete":
            failed += 1
    return 1 if failed else 0


def _state(item: dict, applying: bool) -> str:
    if item.get("error"):
        return "FAILED"
    return "removed" if item.get("removed") else ("would remove" if not applying else "skipped")


def cmd_cleanup_orphans(args: argparse.Namespace) -> int:
    """Source folders left behind by the two cleanup bugs (see
    reconcile.py). Dry run unless --apply: this deletes files, and the
    whole reason these are here is that a delete happened at the wrong
    time before."""
    store = RequestStore(config.DB_PATH)
    qbt = QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD)

    total = 0
    failures = 0

    # Torrents qBittorrent still holds.
    torrents = remove_redundant_sources(store, qbt, apply=args.apply)
    for item in torrents:
        total += item["size_bytes"] or 0
        failures += 1 if item.get("error") else 0
        print(f"{_state(item, args.apply):>12}  torrent  {item['hash'][:8]}  {item['name']}")
        for path in item["filed_paths"]:
            print(f"                       filed: {path}")
        if item.get("error"):
            print(f"                       error: {item['error']}")

    # Folders left on disk after qBittorrent forgot the torrent — no
    # entry left to match on, so these are found by hardlink identity.
    roots = args.root or [str(config.MOVIE_LIBRARY_ROOT), str(config.TV_LIBRARY_ROOT)]
    folders = remove_orphaned_download_dirs(store, roots, apply=args.apply)
    for item in folders:
        total += item["size_bytes"] or 0
        failures += 1 if item.get("error") else 0
        print(f"{_state(item, args.apply):>12}  folder   {item['path']}")
        for video, filed in zip(item["videos"], item["filed_as"]):
            print(f"                       {Path(video).name}")
            print(f"                       filed as: {filed}")
        if item.get("error"):
            print(f"                       error: {item['error']}")

    found = len(torrents) + len(folders)
    if not found:
        print("nothing to clean up — no torrent or folder has filed copies that are all still in place")
        print(f"(looked under: {', '.join(roots)})")
        return 0

    gib = total / 1024**3
    if args.apply:
        removed = sum(1 for i in torrents + folders if i.get("removed"))
        print(f"\nremoved {removed} of {found}, about {gib:.1f} GiB reclaimed")
        return 1 if failures else 0
    print(f"\n{found} item(s) would be removed, about {gib:.1f} GiB. Re-run with --apply.")
    return 0


def _human(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def cmd_convert_to_mkv(args: argparse.Namespace) -> int:
    """Rewraps MP4s in the library as MKVs. A container change, never a
    re-encode: every video and audio stream is copied through byte for
    byte, so nothing is lost and nothing is re-compressed.

    Dry run unless --apply. It reports the total bytes it would rewrite,
    because unlike the audio backfill this one genuinely moves the data
    and the number is worth seeing before you agree to it."""
    roots = [Path(r) for r in (args.root or [])] or [config.MOVIE_LIBRARY_ROOT, config.TV_LIBRARY_ROOT]

    plans = []
    for root in roots:
        if not root.is_dir():
            print(f"skipping {root}: not a directory")
            continue
        print(f"scanning {root}...")
        plans += plan_container_conversion(root)

    convertible = [p for p in plans if p.skipped is None]
    skipped = [p for p in plans if p.skipped is not None]

    # Every byte of an MP4 is read and written back, so a big library is
    # a long sequential job. --limit turns it into batches that can be
    # run between streams; it is safe to stop and resume because a
    # converted file no longer looks like work and a failed one is left
    # exactly as it was.
    remaining = len(convertible)
    if args.limit:
        convertible = convertible[: args.limit]

    for plan in skipped:
        print(f"  SKIP  {plan.source.name[:70]}  — {plan.skipped}")
    if not args.apply:
        for plan in convertible:
            print(f"  WOULD  [{plan.summary}]  {plan.source.name[:70]}")

    total = sum(p.source.stat().st_size for p in convertible if p.source.exists())
    if args.limit and remaining > len(convertible):
        print(f"\n(limited to {len(convertible)} of {remaining}; re-run to continue)")
    if not args.apply:
        print(f"\n{len(convertible)} file(s) would be rewrapped, {len(skipped)} skipped.")
        print(f"About {_human(total)} would be rewritten — a copy, not a re-encode, so nothing is re-compressed.")
        print("Re-run with --apply to do it.")
        return 0

    # Printed as each one finishes, not as a list up front. This reads
    # and writes every byte of every file, so it is a job someone sits
    # and watches — and a list printed before any work started looked
    # like it had already finished.
    failures = 0
    started = time.monotonic()
    moved = 0
    for i, plan in enumerate(convertible, start=1):
        size = plan.source.stat().st_size if plan.source.exists() else 0
        error = apply_container_conversion(plan)
        if error:
            failures += 1
            print(f"  [{i}/{len(convertible)}] FAILED {plan.source.name[:60]}: {error}", flush=True)
            continue
        moved += size
        elapsed = time.monotonic() - started
        rate = moved / elapsed if elapsed > 0 else 0
        left = (total - moved) / rate if rate > 0 else 0
        print(
            f"  [{i}/{len(convertible)}] {_human(size):>9}  {_human(int(rate))}/s  "
            f"~{left / 60:.0f} min left  {plan.source.name[:50]}",
            flush=True,
        )
    done = len(convertible) - failures
    print(f"\n{done} file(s) rewrapped, {failures} failed, {len(skipped)} skipped.")
    print("A failed file is left exactly as it was. Rescan the library in Plex so it picks up the new names.")
    return 1 if failures else 0


def cmd_fix_plex_audio(args: argparse.Namespace) -> int:
    """Makes Plex actually use the audio track the files now ask for.

    fix-audio-defaults corrects the file; this corrects Plex. They are
    two jobs because a file's default flag only decides Plex's *first*
    choice — Plex records what it picked when the item was scanned and
    keeps it, and neither Refresh Metadata nor Analyze revisits that.
    So every file fixed after Plex had already seen it still plays the
    old track, which is precisely what happened here."""
    store = RequestStore(config.DB_PATH)
    try:
        language = args.language or resolve_pipeline_settings(store).preferred_audio_language
        client = PlexClient(store.get_settings().get("plex_client_id") or "obsidian-cli")
        print(f"asking Plex which parts are not on {language}...")
        try:
            choices = plan_audio_selection(store, client, language)
        except PlexError as exc:
            print(f"  {exc}")
            return 1

        for choice in choices:
            arrow = f"{choice.from_language or 'none'} -> {choice.to_language}"
            print(f"  {'SET  ' if args.apply else 'WOULD'}  [{' '.join(choice.languages)}]  {arrow}  {choice.title[:60]}")

        if not args.apply:
            print(f"\n{len(choices)} part(s) would change. Re-run with --apply to set them.")
            print("This changes which track Plex picks, for everyone — no file is touched.")
            return 0

        failures = 0
        for choice in choices:
            error = apply_audio_selection(store, client, choice)
            if error:
                failures += 1
                print(f"  FAILED {choice.title[:60]}: {error}")
        print(f"\n{len(choices) - failures} part(s) changed, {failures} failed.")
        return 1 if failures else 0
    finally:
        store.close()


def cmd_fix_audio_defaults(args: argparse.Namespace) -> int:
    """Backfill: the organiser only sees a file on its way in, so
    everything filed before it learned this keeps whatever default its
    release shipped with.

    Dry run unless --apply, and it prints what it is declining to touch
    as loudly as what it would change — a silent skip here reads as
    "nothing was wrong", which is the one thing it must not be mistaken
    for."""
    language = args.language
    if language is None:
        store = RequestStore(config.DB_PATH)
        try:
            language = resolve_pipeline_settings(store).preferred_audio_language
        finally:
            store.close()
    roots = [Path(r) for r in (args.root or [])] or [config.MOVIE_LIBRARY_ROOT, config.TV_LIBRARY_ROOT]

    plans = []
    for root in roots:
        if not root.is_dir():
            print(f"skipping {root}: not a directory")
            continue
        print(f"scanning {root}...")
        plans += plan_audio_default_fix(root, language)

    changeable = [p for p in plans if p.skipped is None]
    skipped = [p for p in plans if p.skipped is not None]

    for plan in skipped:
        print(f"  SKIP  {plan.summary} {plan.path.name[:70]}  — {plan.skipped}")
    for plan in changeable:
        track = plan.tracks[plan.wanted]
        print(f"  {'FIX ' if args.apply else 'WOULD'}  {plan.summary} -> track {plan.wanted + 1} ({track.language})  {plan.path.name[:70]}")

    if not args.apply:
        print(f"\n{len(changeable)} file(s) would change, {len(skipped)} skipped. Re-run with --apply to write them.")
        print("Nothing is deleted: only which track carries the default flag changes.")
        return 0

    failures = 0
    for plan in changeable:
        error = apply_audio_default_fix(plan)
        if error:
            failures += 1
            print(f"  FAILED {plan.path.name[:70]}: {error}")
    done = len(changeable) - failures
    print(f"\n{done} file(s) changed, {failures} failed, {len(skipped)} skipped.")
    print("Plex reads stream flags when it analyses a file, so refresh metadata on anything that looks unchanged.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    organize_pack_parser = subparsers.add_parser(
        "organize-pack", help="Place every recognizable episode file from a completed pack torrent"
    )
    organize_pack_parser.add_argument("tmdb_id", type=int)
    organize_pack_parser.add_argument("torrent_hash", type=str)
    # Optional, and only used when the hash is no longer in qBittorrent:
    # the release name to find the download's own folder on disk with.
    organize_pack_parser.add_argument("--release-name", type=str, default=None)
    organize_pack_parser.set_defaults(func=cmd_organize_pack)

    retry_parked_parser = subparsers.add_parser(
        "retry-parked-packs",
        help="Re-run organize for packs stuck on 'downloaded, not filed' (dry run unless --apply)",
    )
    retry_parked_parser.add_argument("--apply", action="store_true")
    retry_parked_parser.set_defaults(func=cmd_retry_parked_packs)

    cleanup_parser = subparsers.add_parser(
        "cleanup-orphans",
        help="Remove source torrents whose files are already filed in the library (dry run unless --apply)",
    )
    cleanup_parser.add_argument(
        "--apply", action="store_true", help="Actually delete them; without this, only lists what would go"
    )
    cleanup_parser.add_argument(
        "--root",
        action="append",
        help="Directory to scan for leftover download folders (repeatable). "
        "Defaults to the movie and TV library roots.",
    )
    cleanup_parser.set_defaults(func=cmd_cleanup_orphans)

    audio_parser = subparsers.add_parser(
        "fix-audio-defaults",
        help="Flag the preferred-language audio track as default on files already in the library "
        "(dry run unless --apply)",
    )
    audio_parser.add_argument(
        "--apply", action="store_true", help="Actually write the change; without this, only lists what would happen"
    )
    audio_parser.add_argument(
        "--language",
        default=None,
        help="ISO 639-1 code. Defaults to the household's Settings > Region choice.",
    )
    audio_parser.add_argument(
        "--root",
        action="append",
        help="Directory to scan (repeatable). Defaults to the movie and TV library roots.",
    )
    audio_parser.set_defaults(func=cmd_fix_audio_defaults)

    mkv_parser = subparsers.add_parser(
        "convert-to-mkv",
        help="Rewrap MP4s in the library as MKVs, copying every stream through (dry run unless --apply)",
    )
    mkv_parser.add_argument(
        "--apply", action="store_true", help="Actually rewrap them; without this, only lists what would happen"
    )
    mkv_parser.add_argument(
        "--root",
        action="append",
        help="Directory to scan (repeatable). Defaults to the movie and TV library roots.",
    )
    mkv_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only do this many files. Safe to stop and resume — a converted file is no longer work.",
    )
    mkv_parser.set_defaults(func=cmd_convert_to_mkv)

    plex_audio_parser = subparsers.add_parser(
        "fix-plex-audio",
        help="Make Plex use the preferred-language audio track on items it already scanned "
        "(dry run unless --apply)",
    )
    plex_audio_parser.add_argument("--apply", action="store_true", help="Actually set them in Plex")
    plex_audio_parser.add_argument(
        "--language", default=None, help="ISO 639-1 code. Defaults to Settings > Region."
    )
    plex_audio_parser.set_defaults(func=cmd_fix_plex_audio)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
