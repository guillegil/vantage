"""`vantage push` -- send now the runs the plugin queued for a server it
could not reach.

A session recorded with a backup mode that could not deliver its run
leaves the reports in the plugin's outbox, a file beside the local
database, and the plugin sends a server's queue itself after a later
session reaches that server. This sends every queue, or one server's, on
demand, with the plugin's own sending code and HTTP client, so a queued
run arrives exactly as the session would have sent it: each entry only to
the address it was queued for, deleted once that server acknowledges it,
dropped when the server refuses it outright.

Nothing here imports FastAPI or uvicorn: a test machine with `vantage` and
no `server` extra sends its queue.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from pytest_vantage.outbox import (
    Outbox,
    SendSummary,
    outbox_path,
    send_queued,
)

from vantage.core.config.database import PostgresTarget, database_target
from vantage.local import LocalStoreError, default_database_path

_DEFAULT_TIMEOUT_SECONDS = 10.0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vantage push",
        description=(
            "Send the runs pytest-vantage queued for a server it could not reach, "
            "each to the server it was queued for."
        ),
    )
    parser.add_argument(
        "--database",
        default=None,
        metavar="PATH",
        help=(
            "The local SQLite database the queue sits beside, as given to "
            "--vantage-local-database (default: the one vantage serves without options)."
        ),
    )
    parser.add_argument(
        "--to",
        default=None,
        metavar="URL",
        help="Send only the runs queued for this server address, written as it was configured.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "How long to wait for each run to be acknowledged "
            f"(default {_DEFAULT_TIMEOUT_SECONDS:g})."
        ),
    )
    return parser.parse_args(list(argv))


def _refuse(message: str) -> NoReturn:
    print(f"vantage: {message}", file=sys.stderr)
    raise SystemExit(1)


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or type(exc).__name__


def _database(value: str | None) -> Path:
    """The local database named, or the default one; an empty value counts
    as unset, as for the server's `--database`."""
    if not value:
        try:
            return default_database_path()
        except LocalStoreError as exc:
            _refuse(f"{exc}; pass --database")
    if isinstance(database_target(value), PostgresTarget):
        _refuse(
            "--database names the local SQLite database the queue sits beside; "
            "runs are only ever queued there, never beside a PostgreSQL database"
        )
    return Path(value)


def _summary_line(summary: SendSummary) -> str:
    runs = "run" if summary.sent == 1 else "runs"
    line = f"sent {summary.sent} queued {runs} to {summary.server}"
    if summary.dropped:
        line += f", dropped {len(summary.dropped)} it refused ({', '.join(summary.dropped)})"
    if summary.stopped is not None:
        line += f", then stopped: {summary.stopped}"
    return f"vantage: {line} ({summary.waiting} waiting)"


def push(argv: Sequence[str]) -> int:
    """Send the queued runs `argv` selects and print one line per server.
    The exit status: 0 when none is left waiting for the servers sent to,
    1 otherwise, and 1 with one line on stderr when nothing could be
    tried."""
    args = _parse_args(argv)
    timeout: float = args.timeout
    if not 0 < timeout < math.inf:
        _refuse(f"--timeout must be a positive number of seconds, got {args.timeout!r}")
    path: Path = outbox_path(_database(args.database))
    # Asking what is queued must not create anything.
    if not path.exists():
        print(f"vantage: nothing queued (no queue at {path})")
        return 0

    try:
        outbox = Outbox(path)
    except Exception as exc:
        _refuse(f"cannot open the queue at {path}: {_one_line(exc)}")
    try:
        try:
            servers: tuple[str, ...] = outbox.servers() if args.to is None else (args.to,)
            queued = bool(servers) and (args.to is None or outbox.waiting(args.to) > 0)
        except Exception as exc:
            _refuse(f"cannot read the queue at {path}: {_one_line(exc)}")
        if not queued:
            print("vantage: nothing queued" + ("" if args.to is None else f" for {args.to}"))
            return 0
        left = sum(_send(outbox, server, timeout) for server in servers)
    finally:
        # Every acknowledged run is already deleted, one by one, so a close
        # that fails loses nothing.
        with contextlib.suppress(Exception):
            outbox.close()
    return 0 if left == 0 else 1


def _send(outbox: Outbox, server: str, timeout: float) -> int:
    """Send `server`'s queue, print its line, and return how many runs are
    still waiting for it -- at least one when that cannot be told."""
    try:
        # No budget beyond each run's own timeout: someone asked for the
        # queue to go now, and is watching it go.
        summary: SendSummary = send_queued(outbox, server, timeout=timeout, budget=math.inf)
    except Exception as exc:
        failed = f"vantage: could not send the runs queued for {server}: {_one_line(exc)}"
        try:
            waiting: int = outbox.waiting(server)
        except Exception:
            print(failed)
            return 1
        print(f"{failed} ({waiting} waiting)")
        return waiting
    print(_summary_line(summary))
    left: int = summary.waiting
    return left


__all__ = ["push"]
