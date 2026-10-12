"""The one sqlite3 connection the store shares across threads, and the
results it hands back."""

import sqlite3


class Rows:
    """A statement's result, already read off the cursor.

    The store shares one connection across threads, so a cursor must
    never outlive the lock that produced it: several threads stepping
    cursors on the same connection is exactly the `sqlite3.InterfaceError:
    bad parameter or other API misuse` that searching three requests at
    once surfaced (config.SEARCH_CONCURRENCY). Callers only ever want
    fetchone/fetchall/rowcount/lastrowid or to iterate, all of which this
    serves from memory."""

    __slots__ = ("_rows", "rowcount", "lastrowid")

    def __init__(self, rows, rowcount, lastrowid):
        self._rows = rows
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class SerializedConnection:
    """The one sqlite3 connection, with every statement run to completion
    under the store's lock.

    Only writes used to take that lock, which was survivable while a
    single task touched the store at a time. It isn't once several do —
    and the watcher loops were already concurrent with the queue, so this
    was a narrow race before it became a reliable one."""

    def __init__(self, conn: sqlite3.Connection, lock):
        self._conn = conn
        self._lock = lock

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            # description is None for anything that isn't a SELECT.
            return Rows(cur.fetchall() if cur.description else [], cur.rowcount, cur.lastrowid)

    def executemany(self, sql, seq_of_params):
        with self._lock:
            cur = self._conn.executemany(sql, seq_of_params)
            return Rows([], cur.rowcount, cur.lastrowid)

    def executescript(self, script):
        with self._lock:
            cur = self._conn.executescript(script)
            return Rows([], cur.rowcount, cur.lastrowid)

    def commit(self):
        with self._lock:
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)
