"""The shared `ExecutionStoreContract` run against `PostgresExecutionStore`,
plus what only PostgreSQL can get wrong: the schema and its version, text
it cannot hold or index, time zones, collation, and messages that must
never carry the password.

Every test needs the server `VANTAGE_TEST_POSTGRES_URL` names and is
skipped without it.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from vantage.core.domain.metadata import MAX_METADATA_KEY_CHARS
from vantage.core.ports.storage import (
    ExecutionStore,
    MetadataEntry,
    MetadataFile,
    RunMetadata,
)
from vantage.storage.postgres import PostgresExecutionStore, PostgresOpenError
from vantage.storage.postgres import connection as postgres_connection
from vantage.storage.postgres.connection import scrubbed
from vantage.storage.version import _SCHEMA_VERSION, SchemaVersionError
from vantage_port_contract import (
    ExecutionStoreContract,
    StoredMetadata,
    _execution,
    _failure,
    _result,
    _start_only_execution,
    _vcs,
)

pytest_plugins = ["postgres_fixtures"]
pytestmark = pytest.mark.postgres

_RUN = "a" * 32


class TestPostgresExecutionStore(ExecutionStoreContract):
    @pytest.fixture
    def store(self, postgres_store: PostgresExecutionStore) -> ExecutionStore:
        return postgres_store

    @pytest.fixture
    def stored_metadata(self, postgres_metadata: StoredMetadata) -> StoredMetadata:
        return postgres_metadata


def _query(url: str, statement: str) -> list[tuple[object, ...]]:
    with psycopg.connect(url, autocommit=True) as conn:
        cursor = conn.execute(statement)
        return cursor.fetchall() if cursor.description is not None else []


def _vantage_objects(url: str) -> list[tuple[object, ...]]:
    return _query(
        url,
        "SELECT relname FROM pg_class"
        " WHERE relnamespace = to_regnamespace('vantage') ORDER BY relname",
    )


# -- the schema and its version --


def test_a_fresh_database_gets_the_vantage_schema_stamped_with_the_current_version(
    postgres_url: str,
) -> None:
    PostgresExecutionStore(postgres_url).close()

    stamped = {
        str(key): str(value)
        for key, value in _query(postgres_url, "SELECT key, value FROM vantage.meta")
    }

    assert stamped["schema_version"] == str(_SCHEMA_VERSION)
    assert datetime.fromisoformat(str(stamped["created_at"])).tzinfo is not None


def test_reopening_keeps_what_was_stored(postgres_url: str) -> None:
    first = PostgresExecutionStore(postgres_url)
    first.record_session(_execution(_RUN), results=(), received_at=datetime.now(timezone.utc))
    first.close()

    second = PostgresExecutionStore(postgres_url)
    try:
        assert second.get_execution(_RUN) == _execution(_RUN)
    finally:
        second.close()


@pytest.mark.parametrize(
    ("stamp", "shown"),
    [(str(_SCHEMA_VERSION - 1), str(_SCHEMA_VERSION - 1)), ("7x", "absent"), (None, "absent")],
    ids=["older", "not-a-number", "no-stamp"],
)
def test_a_schema_stamped_with_another_version_is_refused_and_left_as_it_was(
    postgres_url: str, stamp: str | None, shown: str
) -> None:
    PostgresExecutionStore(postgres_url).close()
    if stamp is None:
        _query(postgres_url, "DELETE FROM vantage.meta WHERE key = 'schema_version'")
    else:
        _query(
            postgres_url,
            f"UPDATE vantage.meta SET value = '{stamp}' WHERE key = 'schema_version'",  # noqa: S608
        )
    before = _vantage_objects(postgres_url)

    with pytest.raises(SchemaVersionError) as refused:
        PostgresExecutionStore(postgres_url)

    assert f"schema_version is {shown}," in str(refused.value)
    assert f"requires schema_version {_SCHEMA_VERSION};" in str(refused.value)
    assert _vantage_objects(postgres_url) == before


def test_a_vantage_schema_holding_something_else_is_refused_and_left_as_it_was(
    postgres_url: str,
) -> None:
    _query(postgres_url, "CREATE SCHEMA vantage")
    _query(postgres_url, "CREATE TABLE vantage.run (anything text)")

    with pytest.raises(SchemaVersionError, match="not a vantage schema"):
        PostgresExecutionStore(postgres_url)

    assert _vantage_objects(postgres_url) == [("run",)]


def test_tables_outside_the_vantage_schema_are_neither_refused_nor_touched(
    postgres_url: str,
) -> None:
    _query(postgres_url, "CREATE TABLE public.run (id text)")
    _query(postgres_url, "INSERT INTO public.run VALUES ('theirs')")

    store = PostgresExecutionStore(postgres_url)
    try:
        store.record_session(_execution(_RUN), results=(), received_at=datetime.now(timezone.utc))
    finally:
        store.close()

    assert _query(postgres_url, "SELECT id FROM public.run") == [("theirs",)]


def test_an_empty_vantage_schema_made_beforehand_is_used(postgres_url: str) -> None:
    """Whoever grants the rights to use a schema may create it first, and
    leave the server without the right to create one."""
    _query(postgres_url, "CREATE SCHEMA vantage")

    PostgresExecutionStore(postgres_url).close()

    assert ("run",) in _vantage_objects(postgres_url)


def test_the_tables_and_the_version_stamp_commit_together(
    postgres_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema that fails after every table was created leaves nothing
    behind: a schema without its stamp would be refused on every later
    open."""
    failing = tmp_path / "schema.sql"
    schema = postgres_connection._SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    failing.write_text(f"{schema}\nSELECT 1 / 0;\n", encoding="utf-8")
    monkeypatch.setattr(postgres_connection, "_SCHEMA_SQL_PATH", failing)

    with pytest.raises(PostgresOpenError, match="division by zero"):
        PostgresExecutionStore(postgres_url)

    assert _query(postgres_url, "SELECT to_regnamespace('vantage')") == [(None,)]


def test_a_database_not_encoded_in_utf8_is_refused(
    create_postgres_database: Callable[..., str],
) -> None:
    url = create_postgres_database(encoding="LATIN1")

    with pytest.raises(PostgresOpenError, match="LATIN1; vantage needs a UTF8 database"):
        PostgresExecutionStore(url)

    assert _query(url, "SELECT to_regnamespace('vantage')") == [(None,)]


def test_close_returns_every_connection(postgres_url: str, postgres_admin_url: str) -> None:
    store = PostgresExecutionStore(postgres_url)
    store.list_runs(limit=10, offset=0)
    database = urlsplit(postgres_url).path.lstrip("/")
    connected = f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{database}'"  # noqa: S608

    assert _query(postgres_admin_url, connected) != [(0,)]
    store.close()

    # A backend leaves pg_stat_activity a moment after its client hangs up.
    deadline = time.monotonic() + 10
    while _query(postgres_admin_url, connected) != [(0,)] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _query(postgres_admin_url, connected) == [(0,)]


# -- messages never hold the password --


def test_a_connection_string_libpq_cannot_parse_is_refused_without_its_password() -> None:
    """libpq quotes a URI it cannot parse whole, password included."""
    with pytest.raises(PostgresOpenError) as refused:
        PostgresExecutionStore("postgresql://vantage:hunter2-secret@[::1/vantage")

    assert "hunter2-secret" not in str(refused.value)
    assert "IPv6" in str(refused.value)


def test_a_refused_login_is_one_line_without_the_password(postgres_url: str) -> None:
    # A role that does not exist is refused whether the server checks
    # passwords or trusts local logins.
    parts = urlsplit(postgres_url)
    host = parts.netloc.rpartition("@")[2]
    url = urlunsplit(parts._replace(netloc=f"vantage_no_such_role:not-the-password-7f3a@{host}"))

    with pytest.raises(PostgresOpenError, match='"vantage_no_such_role"') as refused:
        PostgresExecutionStore(url)

    assert "not-the-password-7f3a" not in str(refused.value)
    assert "\n" not in str(refused.value)


def test_an_unreachable_server_is_one_line() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Nothing listens on the port once the probe has closed it.

    with pytest.raises(PostgresOpenError, match="Connection refused") as refused:
        PostgresExecutionStore(f"postgresql://vantage:s3cret@127.0.0.1:{port}/vantage")

    assert "\n" not in str(refused.value)
    assert "s3cret" not in str(refused.value)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("postgresql://u:pw%40x@[::1/db", 'in URI: "postgresql://u:pw%40x@[::1/db"'),
        ("postgresql://u:pw%40x@h/db", "pw%40x and pw@x"),
        ("postgres://h/db?sslmode=x&password=pw%3Dx", "pw%3Dx and pw=x"),
        ("host=h password=pw-x", "pw-x"),
    ],
    ids=["whole-string", "user-information", "query-parameter", "keywords"],
)
def test_scrubbing_masks_the_connection_string_and_every_spelling_of_its_password(
    url: str, message: str
) -> None:
    masked = scrubbed(f"failed:\n\t{message}", url)

    assert "pw" not in masked
    assert "***" in masked
    assert "\n" not in masked


# -- text PostgreSQL cannot hold, and text it cannot index --


def test_nul_is_stored_as_the_replacement_character(
    postgres_store: PostgresExecutionStore, postgres_metadata: StoredMetadata
) -> None:
    now = datetime.now(timezone.utc)
    failure = _failure(failure_message="bad frame \x00\x01")
    postgres_store.record_session(
        _execution(_RUN, vcs=_vcs(commit_subject="Fix\x00")),
        results=(_result("t.py::test_x[\x00]", outcome="failed", failure=failure),),
        received_at=now,
        metadata=RunMetadata(
            files=(MetadataFile(source_file="m\x00.json", content_type="json", status="captured"),),
            entries=(
                MetadataEntry(
                    key="k\x00", value="v\x00", source_file="m\x00.json", status="captured"
                ),
            ),
        ),
    )
    postgres_store.upsert_setting("ns\x00", "Name\x00", value='{"x": "\x00"}', updated_at=now)

    (result,) = postgres_store.get_results(_RUN)
    execution = postgres_store.get_execution(_RUN)
    assert result.identity.node_id == "t.py::test_x[\ufffd]"
    assert result.failure is not None
    assert result.failure.failure_message == "bad frame \ufffd\x01"
    assert execution is not None
    assert execution.vcs is not None
    assert execution.vcs.commit_subject == "Fix\ufffd"
    assert postgres_metadata(_RUN) == RunMetadata(
        files=(MetadataFile(source_file="m\ufffd.json", content_type="json", status="captured"),),
        entries=(
            MetadataEntry(
                key="k\ufffd", value="v\ufffd", source_file="m\ufffd.json", status="captured"
            ),
        ),
    )
    (setting,) = postgres_store.list_settings("ns\ufffd")
    assert (setting.key, setting.value) == ("Name\ufffd", '{"x": "\ufffd"}')


def test_a_lookup_by_a_value_holding_nul_matches_nothing(
    postgres_store: PostgresExecutionStore,
) -> None:
    """Nothing stored holds U+0000, so no lookup by one can match -- and
    none may fail, as binding the value would."""
    now = datetime.now(timezone.utc)
    node_id = "t.py::test_x"
    postgres_store.record_session(
        _start_only_execution(_RUN), results=(_result(node_id),), received_at=now
    )
    nul = "\x00"

    assert postgres_store.get_execution(_RUN + nul) is None
    assert postgres_store.get_run_detail(_RUN + nul) is None
    assert postgres_store.get_run_metadata(_RUN + nul) is None
    assert postgres_store.get_results(_RUN + nul) == []
    assert postgres_store.get_result(_RUN, node_id=node_id + nul) is None
    assert postgres_store.get_catalogue_entry(node_id + nul) is None
    assert postgres_store.list_results(_RUN + nul, limit=10, offset=0).items == ()
    assert postgres_store.list_history(node_id=node_id + nul, limit=10, offset=0).items == ()
    assert postgres_store.get_run_case_outcomes(_RUN + nul) == ()
    assert postgres_store.touch_last_contact(_RUN + nul, now) is False
    assert postgres_store.list_settings(nul) == ()
    assert postgres_store.delete_setting("ns", nul) is False
    page, predating = postgres_store.list_runs_with_metadata_horizon(
        filters=[("k", nul), (nul, "v")], limit=10, offset=0
    )
    assert page.items == ()
    assert predating == (1, 1)


def _unrepeating(first: int, length: int) -> str:
    """`length` distinct characters from U+`first` on, in no run or
    repeated sequence a compressor could shorten."""
    return "".join(chr(first + (index * 7919) % 20000) for index in range(length))


def test_text_past_what_one_index_entry_holds_is_stored_and_found_by_value(
    postgres_store: PostgresExecutionStore, postgres_metadata: StoredMetadata
) -> None:
    """pytest never shortens a parametrize id, and a btree entry holds about
    2.7 kB: a node id, a metadata key and a declared path indexed as text
    would be refused. No character repeats, so compression cannot hide it."""
    node_id = f"t.py::test_x[{_unrepeating(0x4E00, 4000)}]"
    key = _unrepeating(0x10000, MAX_METADATA_KEY_CHARS)
    source_file = f"{_unrepeating(0x4E00, 2000)}.json"
    metadata = RunMetadata(
        files=(MetadataFile(source_file=source_file, content_type="json", status="captured"),),
        entries=(MetadataEntry(key=key, value="1", source_file=source_file, status="captured"),),
    )
    now = datetime.now(timezone.utc)
    for _replay in range(2):
        postgres_store.record_session(
            _execution(_RUN), results=(_result(node_id),), received_at=now, metadata=metadata
        )

    found = postgres_store.get_result(_RUN, node_id=node_id)
    assert found is not None
    assert found.identity.node_id == node_id
    assert postgres_store.count_results() == 1
    assert postgres_store.get_catalogue_entry(node_id) is not None
    assert len(postgres_store.list_history(node_id=node_id, limit=10, offset=0).items) == 1
    assert postgres_metadata(_RUN) == metadata
    page, _predating = postgres_store.list_runs_with_metadata_horizon(
        filters=[(key, "1")], limit=10, offset=0
    )
    assert [entry.execution.identity.value for entry in page.items] == [_RUN]


def test_settings_are_ordered_by_code_point_whatever_the_databases_collation(
    postgres_store: PostgresExecutionStore,
) -> None:
    """A database's default collation is usually linguistic, which puts
    `a` before `B`; the other adapters compare code points."""
    now = datetime.now(timezone.utc)
    for key in ("b", "é", "a", "B"):
        postgres_store.upsert_setting("ns", key, value="{}", updated_at=now)

    assert [setting.key for setting in postgres_store.list_settings("ns")] == ["B", "a", "b", "é"]


# -- time zones --


@pytest.fixture
def store_in_kolkata(postgres_url: str) -> Iterator[PostgresExecutionStore]:
    """A store whose sessions are in a time zone other than UTC, as a
    server configured for local time would have them."""
    store = PostgresExecutionStore(f"{postgres_url}?options=-c%20TimeZone%3DAsia/Kolkata")
    try:
        yield store
    finally:
        store.close()


def test_every_timestamp_reads_back_in_utc_whatever_the_sessions_time_zone(
    store_in_kolkata: PostgresExecutionStore,
) -> None:
    started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    node_id = "t.py::test_x"
    store_in_kolkata.record_session(
        _execution(_RUN, started=started), results=(_result(node_id),), received_at=started
    )
    store_in_kolkata.upsert_setting("ns", "k", value="{}", updated_at=started)

    detail = store_in_kolkata.get_run_detail(_RUN)
    (listed,) = store_in_kolkata.list_runs(limit=10, offset=0).items
    (history,) = store_in_kolkata.list_history(node_id=node_id, limit=10, offset=0).items
    result = store_in_kolkata.get_result(_RUN, node_id=node_id)
    entry = store_in_kolkata.get_catalogue_entry(node_id)
    (setting,) = store_in_kolkata.list_settings("ns")

    assert detail is not None
    assert result is not None
    assert entry is not None
    moments = [
        detail.execution.started_at,
        detail.execution.finished_at,
        detail.last_contact_at,
        listed.execution.started_at,
        listed.last_contact_at,
        history.started_at,
        result.started_at,
        result.finished_at,
        entry.first_seen_at,
        entry.last_seen_at,
        setting.updated_at,
    ]
    assert all(moment is not None and moment.tzinfo is timezone.utc for moment in moments)
    assert detail.execution.started_at == started
