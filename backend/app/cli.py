import argparse
import sys

from app.config import QBIT_HOST, QBIT_PASSWORD, QBIT_PORT, QBIT_USERNAME, TMDB_API_KEY
from app.pipeline import download
from app.qbt import QBTClient
from app.resolve import resolve
from app.tmdb import TMDBClient


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve", help="Resolve a TMDB id into a media identity + query variants")
    resolve_parser.add_argument("tmdb_id", type=int)
    resolve_parser.set_defaults(func=cmd_resolve)

    download_parser = subparsers.add_parser("download", help="Run the full search/match/score/add pipeline for a TMDB id")
    download_parser.add_argument("tmdb_id", type=int)
    download_parser.set_defaults(func=cmd_download)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
