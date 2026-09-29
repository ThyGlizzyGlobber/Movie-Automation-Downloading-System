"""Movie requests: creating, listing, cancelling and rejecting them, and
"This copy is broken" for a movie already on Plex."""

from pathlib import Path

from fastapi import Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.db import RequestRow, RequestStore, SessionRow
from app.plex import local_file_for_title
from app.qbt import QBTClient
from app.resolve import resolve
from app.tv_resolve import resolve_show
from app.tmdb import TMDBClient, TMDBError
from app.worker import Worker
from app.api.deps import get_qbt, get_store, get_tmdb, get_worker, logger, require_can_request, router
from app.api.schemas import RequestOut


class CreateRequest(BaseModel):
    tmdb_id: int
    query: str | None = None
    # frontend migration Part K1/K2: set only from the "Already on Plex"
    # confirmation modal. "overwrite" is rejected below (create_request)
    # unless this tmdb_id has a request this app itself organized on
    # record — never offered against a file only the fuzzy on_plex
    # title/year match found.
    redownload_mode: str | None = None

    @field_validator("redownload_mode")
    @classmethod
    def _known_redownload_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("upgrade", "overwrite"):
            raise ValueError("redownload_mode must be 'upgrade' or 'overwrite'")
        return v


@router.post("/api/requests", status_code=201)
def create_request(
    body: CreateRequest,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    worker: Worker = Depends(get_worker),
    session: SessionRow = Depends(require_can_request),
) -> RequestOut:
    try:
        identity = resolve(body.tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {body.tmdb_id} not found") from exc

    if (
        body.redownload_mode == "overwrite"
        and not store.get_library_items(body.tmdb_id, "movie")
        and store.get_latest_organized_request(body.tmdb_id, ("movie",)) is None
        and local_file_for_title(store, "movie", identity.title, identity.release_year, body.tmdb_id) is None
    ):
        # Defense in depth — never trust the frontend's button state
        # alone. "Overwrite" is only ever offered against a file this
        # app organized itself, or one Plex can point at from here —
        # never a guess from the fuzzy on_plex title/year match.
        raise HTTPException(
            status_code=400, detail="no file on record for this title, and Plex can't point at one from here"
        )

    row = store.create_request(
        tmdb_id=body.tmdb_id,
        title=identity.title,
        release_year=identity.release_year,
        query=body.query,
        requested_by_plex_id=session.plex_user_id,
        requested_by_username=session.username,
        redownload_mode=body.redownload_mode,
        poster_path=identity.poster_path,
    )
    worker.enqueue(row.id)
    return RequestOut.from_row(row)


@router.get("/api/requests")
def list_requests(status: str | None = None, store: RequestStore = Depends(get_store)) -> list[RequestOut]:
    return [RequestOut.from_row(r) for r in store.list_requests(status=status)]


@router.get("/api/requests/{request_id}")
def get_request(request_id: int, store: RequestStore = Depends(get_store)) -> RequestOut:
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    return RequestOut.from_row(row)


@router.post("/api/requests/clear")
def clear_requests(store: RequestStore = Depends(get_store)) -> dict:
    """"Clear My Requests": wipes settled history (reuses the same
    active-job-safe query the automatic retention cleanup runs, with
    days=0 so age never excludes anything terminal)."""
    return {"removed": store.purge_requests_older_than(days=0)}


# A "queued" request hasn't touched qBittorrent (or even started
# resolving/searching) at all yet, so cancelling one is a plain status
# flip — no torrent to delete. worker.py's `_run_one` already re-checks
# `status == "queued"` the moment it's dequeued (guarding against a stale
# queue entry across a restart), so a row cancelled while still sitting in
# the in-memory queue is simply skipped when the worker gets to it,
# no extra wiring needed here. "searching" is deliberately NOT
# cancellable yet — the pipeline is actively running synchronously in a
# worker thread with no cancellation hook, and cancelling the DB row out
# from under it risks the pipeline's own completion overwriting
# "cancelled" back to whatever it concluded once it finishes. A real,
# named gap, same style as this project's other not-yet-solved ones.
_TORRENT_CANCELLABLE_STATUSES = {"downloading", "complete"}


def _cancel_active_torrent(row: RequestRow, store: RequestStore, qbt: QBTClient) -> str | None:
    """Shared by `cancel` and `reject` for a "downloading"/"complete" row:
    validates status/torrent-on-record/still-in-qBittorrent, deletes the
    torrent and its files, marks the request "cancelled", and returns the
    torrent hash (the one extra thing `reject` needs on top of everything
    `cancel` already does)."""
    if row.status not in _TORRENT_CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"cannot cancel a request in status {row.status!r}")
    torrent_hash = (row.result or {}).get("torrent_hash")
    if not torrent_hash:
        if row.status != "downloading":
            raise HTTPException(status_code=409, detail="no torrent on record for this request")
        # The add never pinned down which torrent was ours, so there is
        # nothing to delete; just stop tracking it.
        store.update_status(row.id, "cancelled")
        return None
    if qbt.torrent_info(torrent_hash) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "qBittorrent no longer has this torrent — it most likely finished and was "
                "auto-removed. Nothing was deleted; if the file is still on disk, it needs to "
                "be removed manually."
            ),
        )
    qbt.delete_torrent(torrent_hash, delete_files=True)
    store.update_status(row.id, "cancelled")
    return torrent_hash


@router.post("/api/requests/{request_id}/cancel")
def cancel_request(
    request_id: int, store: RequestStore = Depends(get_store), qbt: QBTClient = Depends(get_qbt)
) -> RequestOut:
    """Cancel from the app's own UI. A "queued" request is just marked
    "cancelled" directly (see the comment above `_TORRENT_CANCELLABLE_STATUSES`).
    A "downloading"/"complete" request instead deletes the torrent *and
    its downloaded files* from qBittorrent (fail safe, not best guess —
    never silently leave orphaned media on disk), then marks it
    "cancelled". Either way the row itself stays — this is "download
    history", not a queue, per the "hidden, never unrecoverable"
    principle.

    If qBittorrent no longer has the torrent at all — most commonly
    because "remove torrent after completion" already auto-removed it —
    there is nothing left to delete, and this app has no other path to the
    file (it never mounts the download folder or the Docker socket, per
    the confirmed architecture). Rather than mark the request "cancelled"
    and imply files were removed when nothing was touched, this fails
    loudly and leaves the request exactly as it was."""
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")

    if row.status == "queued":
        store.update_status(request_id, "cancelled")
        logger.info("request %d (%s) queued -> cancelled (via API)", request_id, row.title)
        return RequestOut.from_row(store.get_request(request_id))

    _cancel_active_torrent(row, store, qbt)
    logger.info("request %d (%s) downloading/complete -> cancelled (via API)", request_id, row.title)
    return RequestOut.from_row(store.get_request(request_id))


@router.post("/api/movies/{tmdb_id}/reject-current")
def reject_current_movie_copy(
    tmdb_id: int,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    session: SessionRow = Depends(require_can_request),
) -> dict:
    """"This copy is broken" for the copy of a movie on Plex. A copy this
    app filed is in the library ledger with its torrent hash and release
    name, however long ago and whether or not the request row still
    exists: the hash and name are blacklisted for the title and the file
    deleted. A copy this app never added is found through Plex instead
    and blacklisted by size and name, for what those are worth. Either
    way the fresh request that follows can't pick the same release."""
    try:
        identity = resolve(tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc

    removed: list[str] = []
    items = store.get_library_items(tmdb_id, "movie")
    if items:
        for item in items:
            if item.get("torrent_hash"):
                store.add_rejected_torrent(tmdb_id, item["torrent_hash"])
            if item.get("release_name"):
                store.add_rejected_release(tmdb_id, item["release_name"])
            elif item.get("size_bytes"):
                store.add_rejected_release(tmdb_id, Path(item["path"]).stem, item["size_bytes"])
            path = Path(item["path"])
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"couldn't delete {path.name}: {exc}") from exc
            store.remove_library_item(item["path"])
            removed.append(path.name)
    else:
        path = local_file_for_title(store, "movie", identity.title, identity.release_year, tmdb_id)
        if path is None:
            raise HTTPException(status_code=409, detail="Plex can't point at a file for this title from here")
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = None
        store.add_rejected_release(tmdb_id, path.stem, size_bytes)
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"couldn't delete {path.name}: {exc}") from exc
        removed.append(path.name)
    logger.info("movie %d (%s): current copy rejected by %s: %s", tmdb_id, identity.title, session.username, ", ".join(removed))
    return {"removed": removed}


@router.post("/api/tv/{tmdb_id}/seasons/{season_number}/reject-current")
def reject_current_season_copy(
    tmdb_id: int,
    season_number: int,
    store: RequestStore = Depends(get_store),
    tmdb: TMDBClient = Depends(get_tmdb),
    session: SessionRow = Depends(require_can_request),
) -> dict:
    """"This copy is broken" for a season already filed.

    The TV counterpart of reject_current_movie_copy, and season-scoped
    because that is how these arrive: a season pack is one encode, so a
    fault in it — jump cuts, sync drift, a wrong cut — is a fault in
    every episode of that season, and rejecting them one at a time
    would be thirteen clicks to say one thing.

    Every file the ledger holds for the season is deleted and its
    torrent hash and release name blacklisted for the show, so the
    re-request that follows cannot land on the same release. The name
    matters as much as the hash: a bad encode re-uploaded under a new
    hash is still the bad encode.

    Only files this app filed, deliberately. A season assembled by hand,
    or by whatever was here before it, has no ledger row saying where it
    came from — and deleting files on a guess is not something to do on
    a household's library.
    """
    try:
        identity = resolve_show(tmdb_id, tmdb)
    except TMDBError as exc:
        raise HTTPException(status_code=404, detail=f"tmdb_id {tmdb_id} not found") from exc

    items = [
        item
        for item in store.get_library_items(tmdb_id, "episode")
        if item.get("season_number") == season_number
    ]
    if not items:
        raise HTTPException(
            status_code=409,
            detail=f"nothing filed by Obsidian for {identity.title} season {season_number}",
        )

    removed: list[str] = []
    for item in items:
        if item.get("torrent_hash"):
            store.add_rejected_torrent(tmdb_id, item["torrent_hash"])
        if item.get("release_name"):
            store.add_rejected_release(tmdb_id, item["release_name"])
        elif item.get("size_bytes"):
            store.add_rejected_release(tmdb_id, Path(item["path"]).stem, item["size_bytes"])
        path = Path(item["path"])
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"couldn't delete {path.name}: {exc}") from exc
        store.remove_library_item(item["path"])
        removed.append(path.name)

    # The episode ledger is what stops a re-request re-filing episodes it
    # thinks it already has, so it has to forget them too.
    show = store.get_show_by_tmdb_id(tmdb_id)
    forgotten = store.forget_show_episodes(show.id, season_number) if show else 0
    logger.info(
        "show %d (%s) season %d: current copy rejected by %s: %d file(s), %d ledger row(s)",
        tmdb_id, identity.title, season_number, session.username, len(removed), forgotten,
    )
    return {"removed": removed, "season_number": season_number}


@router.post("/api/requests/{request_id}/reject")
def reject_request(
    request_id: int, store: RequestStore = Depends(get_store), qbt: QBTClient = Depends(get_qbt)
) -> RequestOut:
    """Stage 15: like `cancel`, but for a torrent that turned out to be
    genuinely defective despite looking like the best candidate on paper —
    a bad encode, audio sync drift, wrong cut, anything the search/scoring
    pipeline's filename/seeder-based signals could never have detected up
    front (confirmed live: a well-seeded, top-scored 2160p "Mutiny" release
    with progressive audio sync drift, 2026-09-14). Deletes the torrent and
    its files the same way `cancel` does, but additionally blacklists this
    exact torrent hash against the request's tmdb_id
    (`RequestStore.add_rejected_torrent`), so a fresh search for the same
    movie/show excludes it and falls through to the next-best-scored
    candidate instead of re-selecting the same defective release.

    Only valid for a "downloading"/"complete" row — a "queued" request has
    no torrent on record yet to reject. A completed download whose torrent
    qBittorrent has since removed (the usual case once seeding is done)
    is still rejected: the hash on record is blacklisted and the files
    this app filed for it are deleted, so the next search really does
    fetch a different copy."""
    row = store.get_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    if row.status == "queued":
        raise HTTPException(status_code=409, detail="a queued request has no torrent yet to reject")
    if row.status not in _TORRENT_CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"cannot reject a request in status {row.status!r}")
    torrent_hash = (row.result or {}).get("torrent_hash")
    if not torrent_hash:
        raise HTTPException(status_code=409, detail="no torrent on record for this request")

    if qbt.torrent_info(torrent_hash) is not None:
        qbt.delete_torrent(torrent_hash, delete_files=True)
    else:
        for path in (row.result or {}).get("organized_paths") or []:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                logger.warning("reject: couldn't delete %s for request %d", path, request_id)
            store.remove_library_item(path)
    store.update_status(row.id, "cancelled")
    store.add_rejected_torrent(row.tmdb_id, torrent_hash)
    # The hash only rules out magnet listings; most winners are direct
    # .torrent links, which carry no hash to compare (Mutiny came straight
    # back, live 2026-09-17). The release's name and size rule it out
    # however it's listed.
    winner = (row.result or {}).get("winner") or {}
    if winner.get("fileName"):
        store.add_rejected_release(row.tmdb_id, winner["fileName"])
    logger.info(
        "request %d (%s) downloading/complete -> cancelled (rejected, torrent hash blacklisted)",
        request_id,
        row.title,
    )
    return RequestOut.from_row(store.get_request(request_id))
