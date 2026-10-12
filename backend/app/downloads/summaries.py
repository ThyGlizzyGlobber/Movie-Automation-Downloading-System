"""How the worker describes a request and what its search found, in the
logs and in each request's stored result."""


def request_label(row) -> str:
    """Log-friendly identifier — "{Show} S01E04" for an episode row,
    "{Show} Season 01"/"{Show} Seasons 01-03"/"{Show} complete series" for
    a Stage 13/14.x bulk-pack row, otherwise just the title, matching
    Stage 14's planned requests-list label change."""
    if row.media_type == "episode" and row.season_number is not None and row.episode_number is not None:
        return f"{row.title} S{row.season_number:02d}E{row.episode_number:02d}"
    if row.media_type == "pack":
        if row.season_range_end is not None:
            scope = f"Seasons {row.season_number:02d}-{row.season_range_end:02d}"
        elif row.season_number is not None:
            scope = f"Season {row.season_number:02d}"
        else:
            scope = "complete series"
        return f"{row.title} {scope}"
    return row.title


def score_summary(score) -> str:
    if score is None:
        return "score=none"
    return (
        f"score=composite:{score.composite},resolution:{score.resolution_score},"
        f"source:{score.source_score},codec:{score.codec_score},"
        f"seeder:{score.seeder_score},container:{score.container_score}"
    )


def result_summary(result) -> dict:
    """Audit-trail snapshot persisted alongside the request: which variant
    matched, the winning candidate, its score breakdown, and the torrent
    hash the download watcher tracks — everything Stage 2's CLI printed,
    now kept for the record per the "hidden, never unrecoverable" principle."""
    summary: dict = {
        "variant_used": result.variant_used,
        "candidates_considered": result.candidates_considered,
        "torrent_hash": result.torrent_hash,
    }
    # Stage 13: PackDownloadResult carries a couple of fields the movie/
    # episode results don't (which literal query string actually matched) —
    # getattr-with-default keeps this one summary builder shared rather
    # than forking a pack-specific copy just for two extra keys.
    query_used = getattr(result, "query_used", None)
    if query_used is not None:
        summary["query_used"] = query_used
    if result.winner is not None:
        summary["winner"] = {
            "fileName": result.winner.get("fileName"),
            "engineName": result.winner.get("engineName"),
            "fileSize": result.winner.get("fileSize"),
            "nbSeeders": result.winner.get("nbSeeders"),
        }
    if result.score is not None:
        summary["score"] = {
            "resolution_score": result.score.resolution_score,
            "source_score": result.score.source_score,
            "codec_score": result.score.codec_score,
            "container_score": result.score.container_score,
            "seeder_score": result.score.seeder_score,
            "composite": result.score.composite,
        }
    if result.error is not None:
        summary["add_error"] = result.error
    return summary
