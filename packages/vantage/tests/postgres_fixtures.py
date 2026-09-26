"""Fresh PostgreSQL databases for the tests that need one.

`VANTAGE_TEST_POSTGRES_URL` points the tests at a server: a URL, on any
database, whose role may create and drop databases. Unset, every test that
asks for a database is skipped with a reason naming the variable, so the
suite passes without a server. Set, each test gets databases of its own,
uniquely named so xdist workers never meet, and dropped afterwards `WITH
(FORCE)`, whatever connections a failing test left open.

- `postgres_url`: the URL of a fresh, empty database.
- `create_postgres_database`: makes more of them, for a test that needs a
  second database or one in another encoding.
- `postgres_store`: a `PostgresExecutionStore` on `postgres_url`, closed
  afterwards.
- `postgres_metadata`: reads back the metadata rows that database holds,
  which no port method returns whole.

Each database sorts text with ICU's `en-US` collation by default, so the
server needs ICU, as the official images have.

A plugin module, as `store_fixtures.py` is: a test module loads it with
``pytest_plugins = ["postgres_fixtures"]``.
"""

from __future__ import annotations

import functools
import os
import uuid
from collections.abc import Iterator
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from vantage.core.ports.storage import MetadataEntry, MetadataFile, RunMetadata
from vantage.storage.postgres import PostgresExecutionStore
from vantage_port_contract import StoredMetadata

POSTGRES_URL_VARIABLE = "VANTAGE_TEST_POSTGRES_URL"


class DatabaseFactory(Protocol):
    def __call__(self, *, encoding: str = "UTF8") -> str:
        """Create a database in `encoding` and return its URL."""
        ...


def _database_url(admin_url: str, name: str) -> str:
    """`admin_url` naming the database `name` instead."""
    return urlunsplit(urlsplit(admin_url)._replace(path=f"/{name}"))


@pytest.fixture
def postgres_admin_url() -> str:
    admin_url = os.environ.get(POSTGRES_URL_VARIABLE)
    if not admin_url:
        pytest.skip(f"{POSTGRES_URL_VARIABLE} is not set; PostgreSQL tests need a server")
    return admin_url


@pytest.fixture
def create_postgres_database(postgres_admin_url: str) -> Iterator[DatabaseFactory]:
    created: list[str] = []

    def create(*, encoding: str = "UTF8") -> str:
        name = f"vantage_test_{uuid.uuid4().hex}"
        # A linguistic default collation, ICU's, whatever the server's C
        # library: musl sorts every locale by code point, which would hide
        # a text key the adapter forgot to give its own collation. Only
        # template0 may be copied into another encoding or locale.
        statement = sql.SQL(
            "CREATE DATABASE {} TEMPLATE template0 ENCODING {}"
            " LOCALE_PROVIDER icu ICU_LOCALE 'en-US' LC_COLLATE 'C' LC_CTYPE 'C'"
        ).format(sql.Identifier(name), sql.Literal(encoding))
        with psycopg.connect(postgres_admin_url, autocommit=True) as admin:
            admin.execute(statement)
        created.append(name)
        return _database_url(postgres_admin_url, name)

    try:
        yield create
    finally:
        with psycopg.connect(postgres_admin_url, autocommit=True) as admin:
            for name in created:
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )


@pytest.fixture
def postgres_url(create_postgres_database: DatabaseFactory) -> str:
    return create_postgres_database()


@pytest.fixture
def postgres_store(postgres_url: str) -> Iterator[PostgresExecutionStore]:
    store = PostgresExecutionStore(postgres_url)
    try:
        yield store
    finally:
        store.close()


def _read_metadata(url: str, run_id: str) -> RunMetadata:
    """The metadata files and entries stored for `run_id`, in no particular
    order, read on a connection of its own as another process would."""
    with psycopg.connect(url) as conn:
        files = conn.execute(
            "SELECT source_file, content_type, status FROM vantage.run_metadata_file"
            " WHERE run_id = %s",
            (run_id,),
        ).fetchall()
        entries = conn.execute(
            "SELECT key, name, value, status, source, source_file, declared"
            " FROM vantage.run_metadata WHERE run_id = %s",
            (run_id,),
        ).fetchall()
    return RunMetadata(
        files=tuple(
            MetadataFile(source_file=source_file, content_type=content_type, status=status)
            for source_file, content_type, status in files
        ),
        entries=tuple(
            MetadataEntry(
                key=key,
                name=name,
                value=value,
                status=status,
                source=source,
                source_file=source_file,
                declared=declared,
            )
            for key, name, value, status, source, source_file, declared in entries
        ),
    )


@pytest.fixture
def postgres_metadata(postgres_url: str) -> StoredMetadata:
    return functools.partial(_read_metadata, postgres_url)
