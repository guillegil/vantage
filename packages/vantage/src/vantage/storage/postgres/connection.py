"""Open a PostgreSQL database for the store: check it, or create the
`vantage` schema in it, over one connection, then open the pool every store
call borrows from.

The check comes first, on a connection of its own, because a pool connects
on background threads: a server that refuses the login would surface only
as a pool timeout, its reason written to a log. Connecting directly turns
it into one error, raised here.

The schema is created, or its version checked, inside one transaction
holding an advisory lock, so of several servers starting on one empty
database exactly one creates it and the others find it stamped. PostgreSQL
DDL is transactional: the tables and the version stamp commit together or
not at all. A `vantage` schema stamped with another version, or holding
objects but no stamp, is refused with `SchemaVersionError` and left as it
was.

No message raised from here holds the password. libpq quotes a connection
string it cannot parse, password and all, so every driver message is
scrubbed of the connection string and of any password in it before it is
raised again.
"""

from __future__ import annotations

import getpass
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool, PoolTimeout

from vantage.storage.version import _SCHEMA_VERSION, SchemaVersionError

_SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")

# A connection attempt's bound when neither the connection string nor
# PGCONNECT_TIMEOUT sets one; psycopg's own, over two minutes, is a long
# wait for a startup refusal.
_CONNECT_TIMEOUT_SECONDS = 10

# How long opening the pool waits for its first connection, and a store
# call for a free one.
_POOL_WAIT_SECONDS = 30.0

# Advisory lock keys are shared by everything that uses the database; a
# first key of vantage's own ("vsch") keeps this lock from meeting another
# application's.
_SCHEMA_LOCK_CLASS = 0x76736368

_SCHEMA_OBJECTS = """
    SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class
        WHERE relnamespace = pg_catalog.to_regnamespace('vantage')
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc
        WHERE pronamespace = pg_catalog.to_regnamespace('vantage')
    )
"""

PgConnection = psycopg.Connection[TupleRow]


class PostgresOpenError(ConnectionError):
    """The store could not be opened: the server could not be reached or
    refused the login, or the database cannot hold a vantage schema. The
    message is one line and never holds the password."""


def connection_kwargs(url: str) -> dict[str, Any]:
    """What every connection is opened with besides `url`: autocommit, so
    a statement outside `transaction()` is a transaction of its own and a
    connection goes back to the pool idle; UTF-8 on the wire whatever
    PGCLIENTENCODING or the connection string ask for, since any other
    client encoding fails every report holding a character it lacks; and a
    connect timeout unless the connection string or PGCONNECT_TIMEOUT
    already sets one."""
    kwargs: dict[str, Any] = {"autocommit": True, "client_encoding": "UTF8"}
    try:
        configured = "connect_timeout" in conninfo_to_dict(url)
    except psycopg.Error:
        # The connection attempt reports what is wrong with the string.
        configured = False
    if not configured and "PGCONNECT_TIMEOUT" not in os.environ:
        kwargs["connect_timeout"] = _CONNECT_TIMEOUT_SECONDS
    return kwargs


def scrubbed(message: str, url: str) -> str:
    """`message` on one line, with `url` and every password it carries
    replaced by `***`."""
    # Longest first, so a password inside the whole string cannot leave
    # part of it behind.
    for secret in sorted(_secrets(url), key=len, reverse=True):
        message = message.replace(secret, "***")
    return " ".join(message.split())


def _secrets(url: str) -> set[str]:
    """`url` itself and each spelling of a password in it: as libpq parses
    it and, for a string libpq cannot parse, as written in the user
    information or a `password=` query parameter, raw and percent-decoded."""
    found = [url]
    try:
        found.append(str(conninfo_to_dict(url).get("password") or ""))
    except psycopg.Error:
        pass
    _scheme, separator, rest = url.partition("://")
    if separator:
        authority, _, query = rest.partition("?")
        authority = authority.partition("/")[0]
        userinfo, at, _host = authority.rpartition("@")
        if at:
            raw = userinfo.partition(":")[2]
            found += [raw, unquote(raw)]
        for parameter in query.split("&"):
            name, _, raw = parameter.partition("=")
            if unquote(name) == "password":
                found += [raw, unquote(raw)]
    return {secret for secret in found if secret}


def prepare_database(url: str) -> None:
    """Create the `vantage` schema in the database `url` names, or check
    the one there, over a connection of its own.

    Raises `SchemaVersionError` for a schema this build cannot use, and
    `PostgresOpenError` for everything else that stops it.
    """
    try:
        with psycopg.connect(url, **connection_kwargs(url)) as conn:
            _require_utf8(conn)
            _ensure_schema(conn)
    except psycopg.Error as exc:
        # Not chained: the driver's own message may hold the password.
        raise PostgresOpenError(scrubbed(str(exc), url)) from None


def open_pool(url: str, *, max_connections: int) -> ConnectionPool[PgConnection]:
    """The pool store calls borrow from, holding at least one connection
    before it is returned. `check` replaces a connection the server has
    dropped -- after a restart, say -- before a store call is handed it,
    instead of failing that call."""
    pool = ConnectionPool(
        url,
        kwargs=connection_kwargs(url),
        min_size=1,
        max_size=max_connections,
        open=False,
        check=ConnectionPool.check_connection,
        name="vantage",
        timeout=_POOL_WAIT_SECONDS,
    )
    try:
        pool.open(wait=True, timeout=_POOL_WAIT_SECONDS)
    except PoolTimeout:
        pool.close()
        raise PostgresOpenError(
            f"no connection to the database within {_POOL_WAIT_SECONDS:g} seconds"
        ) from None
    return pool


def _require_utf8(conn: PgConnection) -> None:
    """Refuse a database whose encoding is not UTF-8: `text_key` relies on
    converting to UTF-8 changing nothing, and only UTF-8 holds every
    character a report can carry."""
    encoding = conn.info.parameter_status("server_encoding")
    if encoding != "UTF8":
        raise PostgresOpenError(
            f"the database's encoding is {encoding}; vantage needs a UTF8 database"
        )


def _ensure_schema(conn: PgConnection) -> None:
    with conn.transaction():
        # Released when the transaction ends, whichever way it ends.
        conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s, 0)", (_SCHEMA_LOCK_CLASS,))
        if _fetch(conn, "SELECT pg_catalog.to_regclass('vantage.meta')") is not None:
            _check_schema_version(conn)
        elif _fetch(conn, _SCHEMA_OBJECTS):
            raise SchemaVersionError(
                "the vantage schema holds objects but no schema_version stamp, so it is "
                "not a vantage schema; nothing was added to it. Choose another database."
            )
        else:
            _create_schema(conn)


def _fetch(conn: PgConnection, query: str) -> object:
    """The one value `query` selects."""
    row = conn.execute(query).fetchone()
    return None if row is None else row[0]


def _check_schema_version(conn: PgConnection) -> None:
    """Refuse a stamp other than `_SCHEMA_VERSION`, older and newer alike:
    a build that does not know a column cannot honour what the build that
    added it assumed."""
    raw = _fetch(conn, "SELECT value FROM vantage.meta WHERE key = 'schema_version'")
    found = _parse_schema_version(raw)
    if found == _SCHEMA_VERSION:
        return
    found_description = "absent" if found is None else str(found)
    raise SchemaVersionError(
        f"the vantage schema's schema_version is {found_description}, but this build "
        f"requires schema_version {_SCHEMA_VERSION}; nothing was changed. Use a database "
        "made by this build."
    )


def _parse_schema_version(raw: object) -> int | None:
    """Absent and not an integer both read as `None`, as the SQLite
    adapter reads them."""
    try:
        return int(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _create_schema(conn: PgConnection) -> None:
    """Create everything and stamp the version, inside the caller's
    transaction. The schema itself is created only when missing: whoever
    made it may have granted the rights to use it without the right to
    create one."""
    if _fetch(conn, "SELECT pg_catalog.to_regnamespace('vantage')") is None:
        conn.execute("CREATE SCHEMA vantage")
    # No parameters, so psycopg sends the file as one simple query, which
    # may hold any number of statements.
    conn.execute(_SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    stamp = "INSERT INTO vantage.meta (key, value) VALUES (%s, %s)"
    conn.execute(stamp, ("schema_version", str(_SCHEMA_VERSION)))
    created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    conn.execute(stamp, ("created_at", created_at))
    created_by = _server_user()
    if created_by is not None:
        conn.execute(stamp, ("created_by", created_by))


def _server_user() -> str | None:
    """The account the server runs as, recorded like the SQLite adapter's
    `created_by`; `None` when it has no name to give."""
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):
        # Before 3.13 an unnamed uid raises `KeyError`, and `ImportError`
        # on Windows; 3.13+ raises `OSError`.
        return None
