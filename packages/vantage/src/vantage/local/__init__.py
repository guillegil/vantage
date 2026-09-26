"""Runs stored on the test machine itself, in a SQLite database, with no
server in between.

The plugin hands a finished session's reports here when it records
locally. Each report goes through the server's own ingestion, encoded as
the plugin's HTTP client encodes it and decoded as the server decodes a
body, so a run stored here reads back exactly as the same run sent to a
server, and `vantage` run with no options on that machine serves it.

Everything here raises `LocalStoreError`, whose message is one line, for
any failure: the plugin turns it into its single warning and the session
goes on, so no other exception may escape.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from vantage.core.config.resolution import default_sqlite_path
from vantage.core.domain.execution import IDENTITY_PATTERN
from vantage.ingestion import ingest
from vantage.ingestion.decode import decode_json
from vantage.ingestion.errors import RejectionError
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage.storage.version import SchemaVersionError

_IDENTITY = re.compile(IDENTITY_PATTERN)


class LocalStoreError(Exception):
    """A run the local database could not take. The message is one line
    and names the database."""


def default_database_path() -> Path:
    """The SQLite database `vantage` serves when given no options:
    ``$XDG_DATA_HOME/vantage/vantage.db``, else
    ``~/.local/share/vantage/vantage.db``. An empty or relative
    ``XDG_DATA_HOME`` is ignored. ``VANTAGE_DATABASE`` plays no part: it
    names a server's database, which may not even be SQLite.

    Raises `LocalStoreError` when there is neither an absolute
    ``XDG_DATA_HOME`` nor a home directory to find.
    """
    path = default_sqlite_path(
        home=_home_directory(), xdg_data_home=os.environ.get("XDG_DATA_HOME")
    )
    if path is None:
        raise LocalStoreError(
            "there is no home directory to put the default database under, "
            "and XDG_DATA_HOME is not an absolute path"
        )
    return path


def store_reports(database: Path, reports: Sequence[Mapping[str, object]]) -> None:
    """Store `reports`, in order, in the SQLite database at `database`,
    creating it if it is missing.

    A new directory is created 0700 and a new database 0600, and a database
    from another schema version is refused untouched, exactly as the
    `vantage` command does. Each report is validated and stored as the
    server would take it; the first that cannot be stops the rest. Raises
    `LocalStoreError` for every failure.
    """
    database = Path(database)
    store = _open(database)
    try:
        for report in reports:
            _store(store, database, report)
    except BaseException:
        # The failure being raised is the one to report; a close failing
        # on top of it would only hide it.
        with contextlib.suppress(Exception):
            store.close()
        raise
    try:
        store.close()
    except Exception as exc:
        raise LocalStoreError(
            _line(f"cannot close the database at {database}: {_said(exc)}")
        ) from exc


def _home_directory() -> Path | None:
    """`None` when there is no home directory to find: `HOME` unset and a
    uid with no passwd entry, as in a container run as an unmapped uid."""
    try:
        return Path.home()
    except (RuntimeError, KeyError):
        return None


def _open(database: Path) -> SqliteExecutionStore:
    try:
        return SqliteExecutionStore(database)
    except SchemaVersionError as exc:
        # Already names the database and what to do about it.
        raise LocalStoreError(_line(str(exc))) from exc
    except Exception as exc:
        # An ancestor this process cannot search or write, a directory at
        # the path, a file that is not a database, a full disk: the OS or
        # sqlite3 message says which.
        raise LocalStoreError(
            _line(f"cannot open the database at {database}: {_said(exc)}")
        ) from exc


def _store(store: SqliteExecutionStore, database: Path, report: Mapping[str, object]) -> None:
    run_id = _run_id(report)
    try:
        # The bytes the plugin's HTTP client would send.
        body = json.dumps(report).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalStoreError(
            _line(f"the report of run {run_id} cannot be encoded as JSON: {_said(exc)}")
        ) from exc
    try:
        # Read the way the server reads a body: lone surrogates and U+0000
        # become U+FFFD, and what JSON cannot carry is refused.
        payload = decode_json(body, replace_lone_surrogates=True)
        ingest(payload, store, received_at=datetime.now(timezone.utc))
    except RejectionError as exc:
        fields = f" ({', '.join(exc.fields)})" if exc.fields else ""
        raise LocalStoreError(
            _line(f"{database} refused the report of run {run_id}: {exc.detail}{fields}")
        ) from exc
    except Exception as exc:
        # A locked or full database, a disk error: sqlite3's message says
        # which.
        raise LocalStoreError(
            _line(f"cannot store run {run_id} in the database at {database}: {_said(exc)}")
        ) from exc


def _run_id(report: Mapping[str, object]) -> str:
    """The run a report names, for a message; never text a report could
    use to forge a line."""
    run = report.get("run") if isinstance(report, Mapping) else None
    run_id = run.get("id") if isinstance(run, Mapping) else None
    return run_id if isinstance(run_id, str) and _IDENTITY.fullmatch(run_id) else "<unnamed>"


def _said(exc: BaseException) -> str:
    """What `exc` says, or its type when it says nothing."""
    return str(exc) or type(exc).__name__


def _line(message: str) -> str:
    """`message` on one line: an OS or sqlite3 message can span several."""
    return " ".join(message.split())


__all__ = ["LocalStoreError", "default_database_path", "store_reports"]
