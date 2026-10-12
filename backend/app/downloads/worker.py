"""Drives requests through queued -> searching -> downloading | no
qualifying results | insufficient free space | failed -> complete | cancelled.

Two independent loops:
- `_process_queue`: one request at a time through the pipeline, guarded by
  an `asyncio.Lock` (the confirmed one-request-at-a-time architecture).
- `_watch_downloads`: polls qBittorrent for requests already added and
  sitting in "downloading", flipping them to "complete", or to "cancelled"
  if the torrent has disappeared from qBittorrent without this app doing it
  (a manual delete, external cleanup). This does *not* hold the pipeline
  lock — `search.start()` alone can already run to a ~55s ceiling, and the
  actual bittorrent transfer happens inside qBittorrent, independent of
  this backend, so watching it shouldn't block the next queued request.

"cancelled" is also reachable directly from the API (api/requests.py's
`POST /api/requests/{id}/cancel`), which deletes the torrent (and its
files) from qBittorrent itself rather than waiting to notice it's gone.
Either path keeps the request row — "download history", not a queue —
per the "hidden, never unrecoverable" principle.

A third loop, `_watch_retention`, purges old *terminal* requests if a
retention policy is set in Settings — history that ages out, not a queue
entry left dangling.

A torrent disappearing from qBittorrent isn't always a deletion: with
qBittorrent's own "remove torrent after completion" option enabled, a
finished download vanishes the same way a cancelled one would. Before
concluding "cancelled", `_check_downloading` asks Plex (if linked) whether
the title actually made it into the library — "fail safe, not best
guess" applied to the one signal qBittorrent can no longer offer once the
torrent is gone.

A 5th loop, `_watch_shows` (Stage 12), is the standing-subscription
scheduler: for each "watching" show it fetches the current latest season
from TMDB, diffs against the `show_episodes` dedup ledger, and creates +
enqueues a normal episode `requests` row for every already-aired episode
not yet handled — reusing `_process_queue`'s single pipeline lock and
`_check_downloading`'s watcher completely unchanged, per the plan's
"no second lock, no distributed queue" call. `check_show()` is the same
code path api/shows.py's `POST /api/shows` calls synchronously for the
immediate post-subscribe catch-up — "add show mid-season" and "scheduled
recheck" are one function, not two. Its wake-up interval is a real
Settings-panel value (`tv_settings.TVScheduleSettings.show_check_interval_hours`),
resolved fresh every cycle, same as pipeline settings elsewhere.

For an episode row specifically, `_check_downloading` additionally gates
on Stage 11's file organizer once the torrent reaches 100%: a successful
`organize_episode()` is what earns "complete" here, not just the torrent
finishing, and a `MediaOrganizerError` (e.g. the torrent evaporated before
this cycle even ran — see project.md's Stage 11 decision-log entry #3, a
real, still-open race with an aggressive `max_ratio` auto-remove) lands on
"downloaded, not filed" instead of a silent, wrong "complete".

A 6th loop, `_watch_episode_rechecks` (Stage 12.x), fixes a real gap the
scheduler above has on its own: `check_show()` marks an episode "handled"
in the dedup ledger the moment it's queued, regardless of whether the
download actually succeeds — so a "no qualifying results" episode would
otherwise never be retried, even after a real release later appears. Gated
behind `episode_recheck_enabled` (off by default — this is new, automatic,
unattended behavior), this loop periodically revisits every ledger entry
for every "watching" show and, per episode, either (a) retries a plain
search/add if nothing was ever successfully downloaded, or (b) peeks at
what's available now (`pipeline.find_best_episode_candidate`, no
`add_torrent` call) and only actually replaces an already-`"complete"`
episode if something genuinely scores higher than what's already in place.
A replacement adds the new torrent first and lets the existing organize-
on-complete path place it (`organize_episode()` is idempotent — it
replaces the file at the same deterministic path); only once that's
confirmed does the old torrent it's replacing get folded into the same
durable cleanup sweep as the new one, via a `replaces_torrent_hash` marker
on the new request — never delete-then-hope-the-replacement-works.

A 7th loop, `_watch_source_cleanup` (Stage 13.x, rebuilt after a real bug),
removes a now-redundant original torrent once its file(s) are safely
organized, after `config.SOURCE_CLEANUP_DELAY_SECONDS`. The original
version fired this off as an in-memory `asyncio.create_task` the instant
organizing succeeded — simple, but silently lost the work forever if the
backend restarted, or the one-shot delete itself failed, anywhere in that
window; found live when a user-side qBittorrent action collided with the
window for one real episode. Rebuilt to be durable like every other loop
here: `mark_organized()` persists what needs cleaning up (which torrent
hash(es), which organized path(s) to re-verify first) straight onto the
request row, `_watch_source_cleanup` re-derives its work fresh from the
database every cycle, and a failed attempt is retried on the next sweep
rather than abandoned. Once a row's cleanup is marked done — success, or a
deliberate, permanent skip (its organized copy has since gone missing) —
it's never revisited again.

A movie row gets the same organize-on-complete gate as an episode row
(`_organize_and_complete_movie`, mirroring `_organize_and_complete_episode`
exactly): `organize_movie()` has to actually rename the movie's folder
into Plex's layout before the request earns "complete". Previously movies
had no automatic organize step at all — `organize_movie()` was Stage
11-era CLI-only tooling, never wired into this watch loop until now.

Where each loop lives: this module holds the state every loop shares and
starts them; their work is mixed into Worker from searching.py (the queue
and its pack fallbacks), watching.py (downloads and filing), shows.py (the
show schedule), rechecks.py (episode rechecks) and housekeeping.py (source
cleanup, orphaned downloads, retention, a box's Plex libraries). A new loop
gets a module of its own, not more methods here.
"""

import asyncio
import logging

from app.box import plex as box_plex
from app.catalogue.tmdb import TMDBClient
from app.catalogue.tvmaze import TVMazeClient
from app.core import config
from app.db import RequestStore
from app.downloads.housekeeping import Housekeeping
from app.downloads.rechecks import Rechecks
from app.downloads.searching import Searching
from app.downloads.shows import ShowSchedule
from app.downloads.watching import DownloadWatching
from app.qbittorrent.client import QBTClient

logger = logging.getLogger(__name__)


class Worker(Searching, DownloadWatching, Housekeeping, ShowSchedule, Rechecks):
    def __init__(self, store: RequestStore, tmdb: TMDBClient, qbt: QBTClient, tvmaze: TVMazeClient | None = None):
        self.store = store
        self.tmdb = tmdb
        self.qbt = qbt
        # Episode release times, which TMDB has no field for. Optional so
        # the existing three-argument construction keeps working; its own
        # lookups are best effort and fall back to TMDB's date.
        self.tvmaze = tvmaze or TVMazeClient()
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        # Bounds how many requests are being searched at once, shared by
        # the queue consumers and the recheck loop so the two together
        # can't exceed it. Replaces the single lock that used to wrap a
        # whole request — see config.SEARCH_CONCURRENCY for why that was
        # the wrong thing to serialise, and pipeline._ADD_LOCK for the
        # part that genuinely still is.
        self._search_slots = asyncio.Semaphore(config.SEARCH_CONCURRENCY)
        self._tasks: list[asyncio.Task] = []
        # Strong references to the after-import Plex nudges, so a task
        # fired and forgotten isn't garbage-collected mid-flight.
        self._audio_nudges: set = set()
        # Request ids sitting in `queue` right now, so enqueue can refuse
        # a duplicate and the orphan sweep can tell what is missing.
        self._pending: set[int] = set()

    def enqueue(self, request_id: int) -> None:
        # Idempotent, so the orphan sweep below can offer every queued
        # row without stacking duplicates behind one another.
        if request_id in self._pending:
            return
        self._pending.add(request_id)
        self.queue.put_nowait(request_id)

    async def _requeue_orphans(self) -> int:
        """Every row the database calls queued, offered to the queue.

        A safety net under the rule "anything that sets a row to queued
        must also enqueue it". That rule was broken once, by the stall
        retry, and the cost was a request that waited forever while
        saying it was waiting its turn. One missed call anywhere is
        enough to do it again, and the failure is silent by nature —
        nothing errors, a card just never moves. So rather than trust
        every future path to remember, this closes the gap within a poll.

        Cheap: one indexed query, and enqueue ignores anything already
        waiting."""
        added = 0
        for request_id in await asyncio.to_thread(self.store.queued_request_ids):
            if request_id not in self._pending:
                self.enqueue(request_id)
                added += 1
        if added:
            logger.warning("re-queued %d request(s) the database had as queued but nothing was waiting on", added)
        return added

    async def start(self) -> None:
        recovered = await asyncio.to_thread(self.store.recover_interrupted)
        if recovered:
            logger.info("boot recovery: marked %d interrupted request(s) as failed", recovered)
        for request_id in await asyncio.to_thread(self.store.queued_request_ids):
            self.enqueue(request_id)
        self._tasks = [
            *(
                asyncio.create_task(self._process_queue(), name=f"worker-queue-{n}")
                for n in range(config.SEARCH_CONCURRENCY)
            ),
            asyncio.create_task(self._watch_downloads(), name="worker-download-watch"),
            asyncio.create_task(self._watch_retention(), name="worker-retention"),
            asyncio.create_task(self._watch_shows(), name="worker-shows"),
            asyncio.create_task(self._watch_episode_rechecks(), name="worker-episode-rechecks"),
            asyncio.create_task(self._watch_source_cleanup(), name="worker-source-cleanup"),
            asyncio.create_task(self._watch_orphaned_downloads(), name="worker-orphan-sweep"),
        ]
        if box_plex.managed():
            self._tasks.append(asyncio.create_task(self._watch_box_libraries(), name="worker-box-libraries"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
