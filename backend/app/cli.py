import argparse
import sys

from app.config import QBIT_HOST, QBIT_PASSWORD, QBIT_PORT, QBIT_USERNAME, TMDB_API_KEY
from app.media_organizer import MediaOrganizerError, organize_episode, organize_movie, select_video_file
from app.pipeline import download, download_episode
from app.qbt import QBTClient
from app.resolve import resolve
from app.tmdb import TMDBClient
from app.tv_resolve import resolve_show


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
        source_path = select_video_file(qbt, args.torrent_hash)
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
        source_path = select_video_file(qbt, args.torrent_hash)
        target_path = organize_movie(identity, source_path)
    except MediaOrganizerError as exc:
        print(f"status:   downloaded, not filed ({exc})")
        return 1

    print(f"movie:    {identity.title} ({identity.release_year})")
    print(f"source:   {source_path}")
    print(f"target:   {target_path}")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
