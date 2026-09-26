"""`vantage.local`: what the plugin calls to store a session on the test
machine, with no server. A run stored here must read back exactly as the
same reports sent to `POST /api/v1/runs`, every failure must be one
`LocalStoreError` line, and the default database must be the one `vantage`
serves with no options.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from vantage.core.config.database import SqliteTarget
from vantage.core.config.resolution import resolve_server_config
from vantage.local import LocalStoreError, default_database_path, store_reports
from vantage.service.app import create_app
from vantage.storage.sqlite_store import SqliteExecutionStore

# Root ignores directory mode bits; Windows ACLs need a different check.
_needs_enforced_mode_bits = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX directory mode bits that this process cannot bypass",
)

_RUN_ID = "a" * 32

# Columns that hold the time a report arrived, which differs between any two
# stores however the report got there.
_ARRIVAL_COLUMNS = frozenset({"received_at", "last_contact_at", "updated_at"})


def _run(finished: bool, run_id: str = _RUN_ID) -> dict[str, Any]:
    return {
        "id": run_id,
        "started_at": "2026-08-15T09:14:02.481930+00:00",
        "finished_at": "2026-08-15T09:14:47.002118+00:00" if finished else None,
        "exit_status": 1 if finished else None,
        "interrupted": False,
        "interrupt_reason": None,
    }


def _result(node_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "file_path": node_id.partition("::")[0],
        "class_name": None,
        "function_name": node_id.rpartition("::")[2],
        "param_id": None,
        "outcome": "passed",
        "duration": 0.25,
        "started_at": "2026-08-15T09:14:03.000000+00:00",
        "finished_at": "2026-08-15T09:14:03.250000+00:00",
        "setup_outcome": "passed",
        "call_outcome": "passed",
        "teardown_outcome": "passed",
        "setup_duration": 0.01,
        "call_duration": 0.2,
        "teardown_duration": 0.04,
        "worker_id": None,
        **fields,
    }


def _session(run_id: str = _RUN_ID) -> list[dict[str, Any]]:
    """A start report, then a finish report carrying everything the server
    rewrites on the way in: a lone surrogate and a U+0000, text past the
    64 KiB bound, an unknown result key, declared YAML metadata and a
    session value."""
    failing = _result(
        "tests/test_\udcff.py::test_bad",
        outcome="failed",
        call_outcome="failed",
        failure_type="AssertionError",
        failure_message="expected \x00 got \ud800",
        traceback="x" * (64 * 1024 + 10),
        captured_stdout="out\x00put",
        marker="slow",
    )
    return [
        {"run": _run(finished=False, run_id=run_id)},
        {
            "run": _run(finished=True, run_id=run_id),
            "results": [_result("tests/test_a.py::test_ok"), failing],
            "vcs": {
                "commit": "c" * 40,
                "branch": "main",
                "commit_subject": "Subject",
                "dirty": False,
                "root": "/repo",
            },
            "metadata": {
                "declaration": "vantage-metadata.json",
                "keys": {"board": {"name": "Board"}},
                "files": [
                    {
                        "path": "board.yaml",
                        "format": "yaml",
                        "status": "captured",
                        "keys": ["board"],
                        "content": "board: rev-b\n",
                    }
                ],
                "values": [{"key": "seed", "value": "42", "status": "captured"}],
            },
        },
    ]


def _rows(database: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table but `meta`, in a fixed order, without the
    columns that record when a report arrived."""
    with contextlib.closing(sqlite3.connect(database)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'meta'"
                " AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"
            )
        ]
        dump: dict[str, list[tuple[Any, ...]]] = {}
        for table in tables:
            columns = [
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})")  # noqa: S608
                if row[1] not in _ARRIVAL_COLUMNS
            ]
            listed = ", ".join(columns)
            dump[table] = conn.execute(
                f"SELECT {listed} FROM {table} ORDER BY {listed}"  # noqa: S608
            ).fetchall()
    return dump


def _served(database: Path, reports: list[dict[str, Any]]) -> None:
    """`reports` sent to a server storing in `database`, as the plugin sends
    them."""
    store = SqliteExecutionStore(database)
    try:
        client = TestClient(create_app(store))
        for report in reports:
            response = client.post(
                "/api/v1/runs",
                content=json.dumps(report).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code in (200, 201), response.text
    finally:
        store.close()


def test_a_session_stored_locally_is_stored_exactly_as_the_server_stores_it(
    tmp_path: Path,
) -> None:
    local = tmp_path / "local" / "vantage.db"
    served = tmp_path / "served" / "vantage.db"

    store_reports(local, _session())
    _served(served, _session())

    stored = _rows(local)
    assert stored == _rows(served)
    assert len(stored["run"]) == 1
    assert len(stored["result"]) == 2
    assert stored["run_metadata"]


def test_the_stored_run_reads_back_through_the_store(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"

    store_reports(database, _session())

    store = SqliteExecutionStore(database)
    try:
        execution = store.get_execution(_RUN_ID)
        assert execution is not None
        assert execution.exit_status == 1
        results = {result.identity.node_id: result for result in store.get_results(_RUN_ID)}
        assert set(results) == {"tests/test_a.py::test_ok", "tests/test_�.py::test_bad"}
        failure = results["tests/test_�.py::test_bad"].failure
        assert failure is not None
        assert failure.failure_message == "expected � got �"
        assert failure.traceback_truncated is True
    finally:
        store.close()


def test_storing_a_session_again_changes_nothing(tmp_path: Path) -> None:
    """The plugin may hand over a session the server also has; replay is
    harmless on either side."""
    database = tmp_path / "vantage.db"
    store_reports(database, _session())
    before = _rows(database)

    store_reports(database, _session())

    assert _rows(database) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_a_new_database_is_created_owner_only(tmp_path: Path) -> None:
    database = tmp_path / "data" / "vantage" / "vantage.db"

    store_reports(database, _session())

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def _refusal(database: Path, reports: Sequence[Mapping[str, object]]) -> str:
    with pytest.raises(LocalStoreError) as refused:
        store_reports(database, reports)
    message = str(refused.value)
    assert "\n" not in message
    assert "Traceback" not in message
    return message


def test_a_database_from_another_schema_version_is_refused_untouched(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"
    SqliteExecutionStore(database).close()
    with contextlib.closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    before = database.read_bytes()

    message = _refusal(database, _session())

    assert str(database) in message
    assert "schema_version is 1" in message
    assert database.read_bytes() == before


def test_a_file_that_is_not_a_vantage_database_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"
    with contextlib.closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE other (id INTEGER)")

    message = _refusal(database, _session())

    assert str(database) in message


def test_a_directory_where_the_database_should_be_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"
    database.mkdir()

    message = _refusal(database, _session())

    assert message.startswith(f"cannot open the database at {database}: ")


@_needs_enforced_mode_bits
def test_a_database_under_a_directory_it_cannot_write_is_refused(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        message = _refusal(locked / "vantage.db", _session())
    finally:
        locked.chmod(0o700)

    assert str(locked / "vantage.db") in message


def test_a_report_the_server_would_refuse_is_refused_naming_the_run_and_field(
    tmp_path: Path,
) -> None:
    database = tmp_path / "vantage.db"
    report = _session()[1]
    del report["run"]["started_at"]

    message = _refusal(database, [report])

    assert message == (
        f"{database} refused the report of run {_RUN_ID}: "
        "The submitted report does not match the expected shape. (run.started_at)"
    )
    store = SqliteExecutionStore(database)
    try:
        assert store.count_executions() == 0
    finally:
        store.close()


def test_the_first_refused_report_stops_the_rest(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"
    refused = {"run": {**_run(finished=True, run_id="b" * 32), "exit_status": "one"}}
    after = {"run": _run(finished=True, run_id="c" * 32)}

    _refusal(database, [{"run": _run(finished=True)}, refused, after])

    store = SqliteExecutionStore(database)
    try:
        assert store.get_execution(_RUN_ID) is not None
        assert store.get_execution("c" * 32) is None
    finally:
        store.close()


def test_a_run_id_that_is_not_one_is_not_repeated(tmp_path: Path) -> None:
    """The message ends up in a warning line; a report's text must not
    forge one."""
    report = {"run": {**_run(finished=True), "id": "x\nforged line"}}

    message = _refusal(tmp_path / "vantage.db", [report])

    assert "forged" not in message
    assert "<unnamed>" in message


def test_a_report_json_cannot_carry_is_refused(tmp_path: Path) -> None:
    report = {"run": _run(finished=True), "results": [_result("t.py::t", duration=float("nan"))]}

    message = _refusal(tmp_path / "vantage.db", [report])

    assert "not valid JSON" in message


def test_a_report_that_cannot_be_encoded_is_refused(tmp_path: Path) -> None:
    report = {"run": _run(finished=True), "extra": {1, 2}}

    message = _refusal(tmp_path / "vantage.db", [report])

    assert message.startswith(f"the report of run {_RUN_ID} cannot be encoded as JSON: ")


def test_a_write_the_database_refuses_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _full(*_args: object, **_kwargs: object) -> bool:
        raise sqlite3.OperationalError("database or disk is full\n(while committing)")

    monkeypatch.setattr(SqliteExecutionStore, "record_session", _full)
    database = tmp_path / "vantage.db"

    message = _refusal(database, _session())

    assert message == (
        f"cannot store run {_RUN_ID} in the database at {database}: "
        "database or disk is full (while committing)"
    )


def test_the_database_is_closed_when_a_report_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []
    real_close = SqliteExecutionStore.close

    def _close(store: SqliteExecutionStore) -> None:
        closed.append(True)
        real_close(store)

    monkeypatch.setattr(SqliteExecutionStore, "close", _close)

    _refusal(tmp_path / "vantage.db", [{"run": {}}])

    assert closed == [True]


def test_sessions_stored_at_once_are_all_kept(tmp_path: Path) -> None:
    """Several pytest sessions on one machine may finish together, each
    storing into the same database with a connection of its own."""
    database = tmp_path / "vantage.db"
    run_ids = [f"{index:032x}" for index in range(1, 7)]
    failures: list[BaseException] = []

    def _store(run_id: str) -> None:
        try:
            store_reports(database, _session(run_id))
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=_store, args=(run_id,)) for run_id in run_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    store = SqliteExecutionStore(database)
    try:
        assert store.count_executions() == len(run_ids)
    finally:
        store.close()


# --- The default database ------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("VANTAGE_DATABASE", raising=False)
    yield home


def test_the_default_database_is_under_xdg_data_home(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    assert default_database_path() == tmp_path / "xdg" / "vantage" / "vantage.db"


@pytest.mark.parametrize("xdg_data_home", ["", "relative/data"], ids=["empty", "relative"])
def test_an_unusable_xdg_data_home_falls_back_to_the_home_directory(
    home: Path, monkeypatch: pytest.MonkeyPatch, xdg_data_home: str
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", xdg_data_home)

    assert default_database_path() == home / ".local" / "share" / "vantage" / "vantage.db"


def test_vantage_database_does_not_move_the_default(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It names a server's database, which may be PostgreSQL."""
    monkeypatch.setenv("VANTAGE_DATABASE", "postgresql://vantage@db.example/vantage")

    assert default_database_path() == home / ".local" / "share" / "vantage" / "vantage.db"


@pytest.mark.parametrize("xdg", [True, False], ids=["xdg-data-home", "home"])
def test_the_default_database_is_the_one_vantage_serves_without_options(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, xdg: bool
) -> None:
    if xdg:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    served = resolve_server_config(
        cli_database=None,
        env_database=None,
        cli_host=None,
        cli_port=None,
        cli_grace_period=None,
        home=Path.home(),
        xdg_data_home=os.environ.get("XDG_DATA_HOME"),
    ).database

    assert served == SqliteTarget(default_database_path())


def test_no_home_directory_and_no_xdg_data_home_is_one_line(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", _no_home)

    with pytest.raises(LocalStoreError, match="no home directory") as refused:
        default_database_path()

    assert "\n" not in str(refused.value)
