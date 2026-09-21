import argparse
import asyncio
import sys
from pathlib import Path

from app import config
from app.config import QBIT_HOST, QBIT_PASSWORD, QBIT_PORT, QBIT_USERNAME, TMDB_API_KEY
from app.db import RequestStore
from app.media_organizer import MediaOrganizerError, organize_episode, organize_movie, organize_pack, select_video_file
from app.pipeline import download, download_episode, download_pack
from app.qbt import QBTClient
from app.reconcile import remove_orphaned_download_dirs, remove_redundant_sources
from app.resolve import resolve
from app.tmdb import TMDBClient
from app.tv_resolve import resolve_show
from app.worker import Worker


def cmd_resolve(args: argparse.Namespace) -> int:
    client = TMDBClient(TMDB_API_KEY)
    identity = resolve(args.tmdb_id, client)

    print(f"tmdb_id:        {identity.tmdb_id}")
    print(f"title:          {identity.title}")
    print(f"original_title: {identity.original_title}")
    print(f"release_year:   {identity.release_year}")
    print("variants:")
    for i, variant in enumerate(identity.variants, start=1):
        print(f"  {i}. {variant}")
    return 0


def cmd_resolve_show(args: argparse.Namespace) -> int:
    client = TMDBClient(TMDB_API_KEY)
    identity = resolve_show(args.tmdb_id, client)

    print(f"tmdb_id:        {identity.tmdb_id}")
    print(f"title:          {identity.title}")
    print(f"original_title: {identity.original_title}")
    print("variants:")
    for i, variant in enumerate(identity.variants, start=1):
        print(f"  {i}. {variant}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    tmdb_client = TMDBClient(TMDB_API_KEY)
    qbt = QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD)
    result = download(args.tmdb_id, tmdb_client, qbt)

    identity = result.identity
    print(f"tmdb_id:  {identity.tmdb_id}")
    print(f"title:    {identity.title} ({identity.release_year})")
    print(f"status:   {result.status}")

    if result.status != "added":
        return 1

    print(f"variant:  {result.variant_used!r}")
    print(f"winner:   {result.winner['fileName']}")
    print(f"engine:   {result.winner['engineName']}")
    print(f"size:     {result.winner.get('fileSize', -1):,} bytes")
    print(f"seeders:  {result.winner.get('nbSeeders', -1)}")
    print("score breakdown:")
    print(f"  resolution: {result.score.resolution_score}")
    print(f"  source:    {result.score.source_score}")
    print(f"  codec:     {result.score.codec_score}")
    print(f"  container: {result.score.container_score}")
    print(f"  seeders:   {result.score.seeder_score}")
    print(f"  composite: {result.score.composite}")
    print(f"candidates considered: {result.candidates_considered}")
    return 0


def cmd_download_episode(args: argparse.Namespace) -> int:
    tmdb_client = TMDBClient(TMDB_API_KEY)
    qbt = QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD)
    identity = resolve_show(args.tmdb_id, tmdb_client)
    result = download_episode(identity, args.season, args.episode, qbt)

    print(f"tmdb_id:  {identity.tmdb_id}")
    print(f"show:     {identity.title}")
    print(f"episode:  S{args.season:02d}E{args.episode:02d}")
    print(f"status:   {result.status}")

    if result.status != "added":
        return 1

    print(f"variant:  {result.variant_used!r}")
    print(f"winner:   {result.winner['fileName']}")
    print(f"engine:   {result.winner['engineName']}")
    print(f"size:     {result.winner.get('fileSize', -1):,} bytes")
    print(f"seeders:  {result.winner.get('nbSeeders', -1)}")
    print("score breakdown:")
    print(f"  resolution: {result.score.resolution_score}")
    print(f"  source:    {result.score.source_score}")
    print(f"  codec:     {result.score.codec_score}")
    print(f"  container: {result.score.container_score}")
    print(f"  seeders:   {result.score.seeder_score}")
    print(f"  composite: {result.score.composite}")
    print(f"candidates considered: {result.candidates_considered}")
    return 0


def cmd_bulk_download(args: argparse.Namespace) -> int:
    """Manual dev entry point for Stage 13's pack pipeline, same reasoning
    as cmd_download_episode: exercises download_pack() directly against
    real TMDB/qBittorrent without going through the API/worker queue,
    useful for live validation ahead of (or independent of) exercising the
    real `POST /api/shows/{id}/bulk-download` route."""
    if args.scope == "season" and args.season is None:
        print("error: --season is required when scope is 'season'")
        return 2

    tmdb_client = TMDBClient(TMDB_API_KEY)
    qbt = QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD)
    identity = resolve_show(args.tmdb_id, tmdb_client)
    result = download_pack(identity, args.scope, qbt, season=args.season)

    print(f"tmdb_id:  {identity.tmdb_id}")
    print(f"show:     {identity.title}")
    print(f"scope:    {args.scope}" + (f" (season {args.season})" if args.season else ""))
    print(f"status:   {result.status}")

    if result.status != "added":
        return 1

    print(f"variant:  {result.variant_used!r}")
    print(f"query:    {result.query_used!r}")
    print(f"winner:   {result.winner['fileName']}")
    print(f"engine:   {result.winner['engineName']}")
    print(f"size:     {result.winner.get('fileSize', -1):,} bytes")
    print(f"seeders:  {result.winner.get('nbSeeders', -1)}")
    print(f"torrent hash: {result.torrent_hash}")
    print(f"candidates considered: {result.candidates_considered}")
    return 0


def cmd_organize_pack(args: argparse.Namespace) -> int:
    """Manual dev entry point for Stage 13's organize_pack(), mirroring
    cmd_organize_episode: run against a real, already-completed pack
    torrent's hash (add one first with `bulk-download`, wait for it to
    finish in qBittorrent, then pass its hash here).

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


def cmd_organize_episode(args: argparse.Namespace) -> int:
    """Manual dev entry point for Stage 11's organizer, mirroring how
    Stage 10's download-episode shipped CLI-only ahead of any worker/API
    wiring: a persisted episode "downloading" row for the watcher to gate
    on doesn't exist until Stage 12 adds the schema for one. Runs against a
    real, already-completed torrent (add one first with `download-episode`,
    wait for it to finish in qBittorrent, then pass its hash here)."""
    tmdb_client = TMDBClient(TMDB_API_KEY)
    qbt = QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD)
    identity = resolve_show(args.tmdb_id, tmdb_client)

    try:
        source_path = select_video_file(qbt, args.torrent_hash, config.QBIT_TV_SAVE_PATH, config.TV_LIBRARY_ROOT)
        target_path = organize_episode(identity, args.season, args.episode, source_path)
    except MediaOrganizerError as exc:
        print(f"status:   downloaded, not filed ({exc})")
        return 1

    print(f"show:     {identity.title}")
    print(f"episode:  S{args.season:02d}E{args.episode:02d}")
    print(f"source:   {source_path}")
    print(f"target:   {target_path}")
    return 0


def cmd_organize_movie(args: argparse.Namespace) -> int:
    """Manual dev entry point, same reasoning as cmd_organize_episode: no
    worker/API wiring yet, so this is how a movie's folder gets renamed to
    Plex's `<Title> (<year>) {tmdb-<id>}` shape until Stage 12-equivalent
    wiring exists for movies too."""
    tmdb_client = TMDBClient(TMDB_API_KEY)
    qbt = QBTClient(QBIT_HOST, QBIT_PORT, QBIT_USERNAME, QBIT_PASSWORD)
    identity = resolve(args.tmdb_id, tmdb_client)

    try:
        source_path = select_video_file(
            qbt, args.torrent_hash, config.QBIT_MOVIE_SAVE_PATH, config.MOVIE_LIBRARY_ROOT
        )
        target_path = organize_movie(identity, source_path)
    except MediaOrganizerError as exc:
        print(f"status:   downloaded, not filed ({exc})")
        return 1

    print(f"movie:    {identity.title} ({identity.release_year})")
    print(f"source:   {source_path}")
    print(f"target:   {target_path}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve", help="Resolve a TMDB id into a media identity + query variants")
    resolve_parser.add_argument("tmdb_id", type=int)
    resolve_parser.set_defaults(func=cmd_resolve)

    resolve_show_parser = subparsers.add_parser(
        "resolve-show", help="Resolve a TMDB TV id into a show identity + query variants"
    )
    resolve_show_parser.add_argument("tmdb_id", type=int)
    resolve_show_parser.set_defaults(func=cmd_resolve_show)

    download_parser = subparsers.add_parser("download", help="Run the full search/match/score/add pipeline for a TMDB id")
    download_parser.add_argument("tmdb_id", type=int)
    download_parser.set_defaults(func=cmd_download)

    download_episode_parser = subparsers.add_parser(
        "download-episode", help="Run the episode-aware search/match/score/add pipeline for one episode"
    )
    download_episode_parser.add_argument("tmdb_id", type=int)
    download_episode_parser.add_argument("season", type=int)
    download_episode_parser.add_argument("episode", type=int)
    download_episode_parser.set_defaults(func=cmd_download_episode)

    bulk_download_parser = subparsers.add_parser(
        "bulk-download", help="Run the season/complete-series pack search/match/score/add pipeline"
    )
    bulk_download_parser.add_argument("tmdb_id", type=int)
    bulk_download_parser.add_argument("scope", choices=["season", "series"])
    bulk_download_parser.add_argument("--season", type=int, default=None, help="Required when scope is 'season'")
    bulk_download_parser.set_defaults(func=cmd_bulk_download)

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

    organize_episode_parser = subparsers.add_parser(
        "organize-episode", help="Place an already-completed episode torrent's file into Plex's library layout"
    )
    organize_episode_parser.add_argument("tmdb_id", type=int)
    organize_episode_parser.add_argument("season", type=int)
    organize_episode_parser.add_argument("episode", type=int)
    organize_episode_parser.add_argument("torrent_hash", type=str)
    organize_episode_parser.set_defaults(func=cmd_organize_episode)

    organize_movie_parser = subparsers.add_parser(
        "organize-movie", help="Rename an already-completed movie torrent's folder into Plex's library shape"
    )
    organize_movie_parser.add_argument("tmdb_id", type=int)
    organize_movie_parser.add_argument("torrent_hash", type=str)
    organize_movie_parser.set_defaults(func=cmd_organize_movie)

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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
