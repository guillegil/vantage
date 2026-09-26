"""The outbox: runs a server could not take, kept to be sent later.

A SQLite file of its own next to the local database (`outbox_path`), owned
by the plugin and written with the standard library's `sqlite3`. It is not
a vantage database and shares nothing with the vantage schema: each entry
is one run's report bodies exactly as they would have been sent, with the
server address they were meant for.

- **Owner-only.** The reports carry failure text and metadata, so the file
  is created at 0600, in a directory created at 0700 if missing; an
  existing directory or file keeps the mode its owner chose.
- **Only to its own server.** An entry is sent to the address it was queued
  for, compared exactly as configured, never to another.
- **Concurrent flushers.** Several pytest sessions, or `vantage push`, may
  send at once. Each claims an entry in a write transaction before sending
  it, for as long as sending it can take; a flusher killed mid-send leaves
  a claim that lapses, and the entry is taken up again. A run sent twice is
  harmless: the server's writes are idempotent.
- **Bounded.** At most `MAX_ENTRIES` entries and `MAX_REPORT_BYTES` of report
  text; queueing past either drops the oldest entries (`Outbox.evicted`).
"""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib import error as urllib_error

from pytest_vantage.transport import send

MAX_ENTRIES = 1000
MAX_REPORT_BYTES = 256 * 1024 * 1024

# Marks the file as an outbox, so some other SQLite file that happens to sit
# at the path is refused rather than written into.
_OUTBOX_VERSION = 1

# Added to the longest a claimed entry can take to send, so a flusher that
# is slow but alive never has its claim taken over.
_CLAIM_MARGIN_SECONDS = 60.0

# A second process holding the write lock is waited for rather than failing
# at once; no transaction here spans a network request, so the wait is short.
_BUSY_TIMEOUT_SECONDS = 5.0

# Request Timeout and Too Many Requests: a busy server, or a proxy in front
# of it, asking for the same request again later. Dropping a run over one
# would lose every queued run that a burst of sending trips a rate limit with.
_RETRY_LATER = frozenset({408, 429})

_SCHEMA = (
    """
    CREATE TABLE entry (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        server       TEXT    NOT NULL,
        run_id       TEXT    NOT NULL,
        reports      TEXT    NOT NULL,
        report_count INTEGER NOT NULL,
        size         INTEGER NOT NULL,
        queued_at    TEXT    NOT NULL,
        attempts     INTEGER NOT NULL DEFAULT 0,
        last_error   TEXT,
        claimed_until REAL
    )
    """,
    "CREATE INDEX entry_by_server ON entry (server, id)",
    f"PRAGMA user_version = {_OUTBOX_VERSION}",
)


class OutboxError(Exception):
    """The outbox cannot be opened, read or written, or a run does not fit
    in it; the message is one line."""


def outbox_path(database: Path) -> Path:
    """Where the outbox belonging to the local database `database` lives."""
    return database.with_name(database.name + "-outbox")


def unreachable(exc: BaseException) -> bool:
    """Whether a failed report never got an answer from the server: no
    connection, a connection that broke, or no complete answer in time.
    A timeout counts, although the server may have stored the report: one
    sent again is stored once."""
    if isinstance(exc, urllib_error.HTTPError):
        return False
    return isinstance(exc, (OSError, http.client.HTTPException))


def worth_retrying(exc: BaseException) -> bool:
    """Whether sending the same report later could succeed: the server was
    unreachable, answered 5xx, or asked for the request again later. Any
    other 4xx, a redirect or an answer that does not acknowledge the run
    would be the same the next time."""
    if isinstance(exc, urllib_error.HTTPError):
        return exc.code >= 500 or exc.code in _RETRY_LATER
    return unreachable(exc)


def _rejected(exc: BaseException) -> bool:
    return (
        isinstance(exc, urllib_error.HTTPError)
        and 400 <= exc.code < 500
        and exc.code not in _RETRY_LATER
    )


def _server_error(exc: BaseException) -> bool:
    return isinstance(exc, urllib_error.HTTPError) and exc.code >= 500


@dataclass(frozen=True)
class SendSummary:
    """What `send_queued` did for one server."""

    server: str
    sent: int
    """Entries acknowledged and deleted."""
    dropped: tuple[str, ...]
    """Run ids of the entries the server rejected with a 4xx other than 408
    or 429, now deleted."""
    waiting: int
    """Entries still queued for `server`."""
    stopped: str | None
    """Why sending stopped before the queue was done, or `None`."""
    unreadable: tuple[str, ...] = ()
    """Run ids of the entries whose reports could not be read back from the
    file, now deleted."""


@dataclass(frozen=True)
class _Claimed:
    id: int
    run_id: str
    reports: list[dict[str, Any]] | None
    """`None` when what the file holds is not a list of reports."""


class Outbox:
    """One open outbox file; `Outbox(path)` creates it if missing."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.evicted: list[str] = []
        """Run ids `enqueue` dropped to stay within bounds, oldest first."""
        with self._errors("open"):
            try:
                os.makedirs(self.path.parent, mode=0o700)
            except FileExistsError:
                pass
            if os.name == "posix":
                # Before `sqlite3` sees the path: it would create the file at
                # 0644 under a permissive umask.
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                except FileExistsError:
                    pass
                else:
                    os.close(fd)
            self._conn = sqlite3.connect(
                str(self.path), isolation_level=None, timeout=_BUSY_TIMEOUT_SECONDS
            )
        try:
            with self._errors("open"):
                self._prepare()
        except BaseException:
            self._conn.close()
            raise

    def __enter__(self) -> Outbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _errors(self, doing: str) -> Iterator[None]:
        try:
            yield
        except (OSError, sqlite3.Error) as exc:
            raise OutboxError(f"cannot {doing} the outbox at {self.path}: {exc}") from None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        # IMMEDIATE takes the write lock up front, so reading what to change
        # and changing it is one step for every process sharing the file.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def _prepare(self) -> None:
        with self._transaction() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if not tables and version == 0:
                for statement in _SCHEMA:
                    conn.execute(statement)
            elif version != _OUTBOX_VERSION or "entry" not in tables:
                raise OutboxError(f"{self.path} is not a vantage outbox; nothing was changed")

    def enqueue(self, server: str, run_id: str, reports: Sequence[Mapping[str, object]]) -> int:
        """Queue one run's `reports`, in send order, for `server`, and
        return how many entries now wait for `server`.

        When the new entry would break a bound, the oldest entries, for
        whichever server, are dropped first and their run ids added to
        `evicted`. A run too large for the outbox on its own is refused
        with `OutboxError`, and nothing is dropped for it."""
        text = json.dumps([dict(report) for report in reports])
        size = len(text.encode("utf-8"))
        if size > MAX_REPORT_BYTES:
            raise OutboxError(
                f"run {run_id} is larger than the outbox's bound of {MAX_REPORT_BYTES} bytes"
            )
        queued_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._errors("write to"), self._transaction() as conn:
            count, total = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM entry"
            ).fetchone()
            evicted: list[tuple[int, str]] = []
            oldest = conn.execute("SELECT id, run_id, size FROM entry ORDER BY id")
            while count + 1 > MAX_ENTRIES or total + size > MAX_REPORT_BYTES:
                row = oldest.fetchone()
                if row is None:
                    break
                evicted.append((row[0], row[1]))
                count -= 1
                total -= row[2]
            oldest.close()
            conn.executemany("DELETE FROM entry WHERE id = ?", [(i,) for i, _ in evicted])
            conn.execute(
                "INSERT INTO entry (server, run_id, reports, report_count, size, queued_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (server, run_id, text, len(reports), size, queued_at),
            )
            waiting = self._count(server)
        self.evicted.extend(run for _, run in evicted)
        return waiting

    def _count(self, server: str | None) -> int:
        if server is None:
            row = self._conn.execute("SELECT COUNT(*) FROM entry").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM entry WHERE server = ?", (server,)
            ).fetchone()
        count: int = row[0]
        return count

    def waiting(self, server: str | None = None) -> int:
        """Entries queued for `server`, or for any server."""
        with self._errors("read"):
            return self._count(server)

    def servers(self) -> tuple[str, ...]:
        """Every address an entry waits for, the longest-waiting first."""
        with self._errors("read"):
            rows = self._conn.execute(
                "SELECT server FROM entry GROUP BY server ORDER BY MIN(id)"
            ).fetchall()
        return tuple(row[0] for row in rows)

    def _claim_next(
        self, server: str, after: int, *, timeout: float, budget: float
    ) -> _Claimed | None:
        """The oldest entry for `server` past id `after` that no live
        flusher holds, now held by this one, or `None`.

        Held for as long as sending it can take -- every report its full
        `timeout`, within `budget` -- so a killed flusher's claim lapses no
        later than a live one's would."""
        with self._errors("read"), self._transaction() as conn:
            now = time.time()
            row = conn.execute(
                "SELECT id, run_id, report_count FROM entry "
                "WHERE server = ? AND id > ? AND (claimed_until IS NULL OR claimed_until < ?) "
                "ORDER BY id LIMIT 1",
                (server, after, now),
            ).fetchone()
            if row is None:
                return None
            entry_id, run_id, report_count = row
            held_for = min(budget, report_count * timeout) + _CLAIM_MARGIN_SECONDS
            conn.execute(
                "UPDATE entry SET claimed_until = ? WHERE id = ?", (now + held_for, entry_id)
            )
            (text,) = conn.execute("SELECT reports FROM entry WHERE id = ?", (entry_id,)).fetchone()
        return _Claimed(entry_id, run_id, _reports(text))

    def _delete(self, entry_id: int) -> None:
        with self._errors("write to"):
            self._conn.execute("DELETE FROM entry WHERE id = ?", (entry_id,))

    def _release(self, entry_id: int, error: str | None) -> None:
        """Give a claimed entry back: with `error`, as a failed attempt."""
        with self._errors("write to"):
            if error is None:
                self._conn.execute(
                    "UPDATE entry SET claimed_until = NULL WHERE id = ?", (entry_id,)
                )
            else:
                self._conn.execute(
                    "UPDATE entry SET claimed_until = NULL, attempts = attempts + 1, "
                    "last_error = ? WHERE id = ?",
                    (error, entry_id),
                )


def _reports(text: object) -> list[dict[str, Any]] | None:
    """An entry's reports as `enqueue` wrote them, or `None` when the file
    no longer holds a JSON list of objects there."""
    try:
        reports = json.loads(text) if isinstance(text, (str, bytes)) else None
    except (ValueError, RecursionError):
        return None
    if isinstance(reports, list) and all(isinstance(report, dict) for report in reports):
        return reports
    return None


class _OutOfTimeError(Exception):
    pass


def send_queued(outbox: Outbox, server: str, *, timeout: float, budget: float) -> SendSummary:
    """Send `server`'s queued runs, oldest first, each report bounded by
    `timeout` and all of them by `budget` seconds (`math.inf` for none).

    An acknowledged run is deleted. One the server rejects with a 4xx other
    than 408 or 429 could never succeed, and is deleted too, its run id in
    `dropped`; so is one whose reports no longer read back from the file,
    in `unreadable`. A 5xx leaves the run queued and goes on to the next,
    since it may be that run's own problem. Anything else leaves the run
    queued and stops: an unreachable server, a 408 or 429 asking for it
    again later, or an answer that is not a vantage server's.
    """
    deadline = time.monotonic() + budget
    ran_out = f"the {budget:g}s allowed for sending ran out"
    sent = 0
    dropped: list[str] = []
    unreadable: list[str] = []
    stopped: str | None = None
    after = 0
    while stopped is None:
        claimed = outbox._claim_next(server, after, timeout=timeout, budget=budget)
        if claimed is None:
            break
        after = claimed.id
        if claimed.reports is None:
            # Damaged in the file: it could never be sent, and left in place
            # it would stop every sender at the head of the queue.
            outbox._delete(claimed.id)
            unreadable.append(claimed.run_id)
            continue
        try:
            for report in claimed.reports:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _OutOfTimeError
                bound = min(timeout, remaining)
                try:
                    send(server, report, timeout=bound)
                except TimeoutError:
                    if bound < timeout:
                        raise _OutOfTimeError from None
                    raise
        except _OutOfTimeError:
            outbox._release(claimed.id, None)
            stopped = ran_out
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            if _rejected(exc):
                outbox._delete(claimed.id)
                dropped.append(claimed.run_id)
                continue
            outbox._release(claimed.id, str(exc) or type(exc).__name__)
            if not _server_error(exc):
                stopped = (
                    f"{server} is unreachable ({exc})"
                    if unreachable(exc)
                    else f"{server} did not take run {claimed.run_id} ({exc})"
                )
        except BaseException:
            # Ctrl-C stops sending, but the run is given back now rather
            # than held until its claim lapses, a minute or more away.
            with suppress(Exception):
                outbox._release(claimed.id, None)
            raise
        else:
            outbox._delete(claimed.id)
            sent += 1
    return SendSummary(
        server, sent, tuple(dropped), outbox.waiting(server), stopped, tuple(unreadable)
    )


__all__ = [
    "MAX_ENTRIES",
    "MAX_REPORT_BYTES",
    "Outbox",
    "OutboxError",
    "SendSummary",
    "outbox_path",
    "send_queued",
    "unreachable",
    "worth_retrying",
]
