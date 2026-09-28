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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import vantage.local
from fastapi.testclient import TestClient
from password_fixtures import cheap_hash
from vantage.core.config.database import SqliteTarget
from vantage.core.config.resolution import resolve_server_config
from vantage.core.domain.projects import DEFAULT_PROJECT, VIEWER_ROLE
from vantage.ingestion import Ingested, ingest
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
# stores however the report got there, and the time a project was made:
# `default` with each database, any other when it was first named or added.
_ARRIVAL_COLUMNS = frozenset({"received_at", "last_contact_at", "updated_at", "created_at"})


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


def _served(database: Path, reports: list[dict[str, Any]], *, projects: Sequence[str] = ()) -> None:
    """`reports` sent to a server storing in `database`, as the plugin sends
    them, once its admin has added `projects`."""
    store = SqliteExecutionStore(database)
    try:
        for project in projects:
            store.create_project(project, created_at=datetime.now(timezone.utc))
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


def _in_project(project: str | None, run_id: str = _RUN_ID) -> list[dict[str, Any]]:
    """`_session`, every report naming `project`, as the plugin sends it."""
    return [{**report, "project": project} for report in _session(run_id)]


def _projects(database: Path) -> list[str]:
    store = SqliteExecutionStore(database)
    try:
        return [project.name for project in store.list_projects()]
    finally:
        store.close()


def _project_of(database: Path, run_id: str) -> str | None:
    store = SqliteExecutionStore(database)
    try:
        detail = store.get_run_detail(run_id)
        return None if detail is None else detail.project
    finally:
        store.close()


def test_a_report_naming_a_project_the_database_lacks_creates_it_and_stores_the_run_there(
    tmp_path: Path,
) -> None:
    """Nobody but the database's owner could add the project, and the local
    copy of a run a server refused for its project must still land."""
    database = tmp_path / "vantage.db"

    store_reports(database, _in_project("firmware"))

    assert _projects(database) == [DEFAULT_PROJECT, "firmware"]
    assert _project_of(database, _RUN_ID) == "firmware"
    store = SqliteExecutionStore(database)
    try:
        assert store.get_catalogue_entry("tests/test_a.py::test_ok", project="firmware")
        assert (
            store.get_catalogue_entry("tests/test_a.py::test_ok", project=DEFAULT_PROJECT) is None
        )
    finally:
        store.close()


def test_a_run_in_a_named_project_is_stored_as_a_server_with_that_project_stores_it(
    tmp_path: Path,
) -> None:
    local = tmp_path / "local" / "vantage.db"
    served = tmp_path / "served" / "vantage.db"

    store_reports(local, _in_project("firmware"))
    _served(served, _in_project("firmware"), projects=["firmware"])

    stored = _rows(local)
    assert stored == _rows(served)
    assert len(stored["project"]) == 2


def test_a_report_refused_for_naming_another_project_leaves_no_project_behind(
    tmp_path: Path,
) -> None:
    """A run never moves, so a later report of it naming another project is
    refused -- and a refused report changes nothing, the projects included."""
    database = tmp_path / "vantage.db"
    store_reports(database, _in_project("firmware"))

    with pytest.raises(LocalStoreError):
        store_reports(database, _in_project("boards"))

    assert _projects(database) == [DEFAULT_PROJECT, "firmware"]
    assert _project_of(database, _RUN_ID) == "firmware"


def test_a_later_session_in_a_project_already_made_goes_to_it(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"

    store_reports(database, _in_project("firmware"))
    store_reports(database, _in_project("firmware", run_id="b" * 32))

    assert _projects(database) == [DEFAULT_PROJECT, "firmware"]
    assert _project_of(database, "b" * 32) == "firmware"


@pytest.mark.parametrize("named", [False, True], ids=["absent", "null"])
def test_a_report_naming_no_project_goes_to_default_and_makes_none(
    tmp_path: Path, named: bool
) -> None:
    database = tmp_path / "vantage.db"
    reports = _in_project(None) if named else _session()

    store_reports(database, reports)

    assert _projects(database) == [DEFAULT_PROJECT]
    assert _project_of(database, _RUN_ID) == DEFAULT_PROJECT


@pytest.mark.parametrize(
    "project",
    ["", "Firmware", "../x", "f" * 65, "fw\x00", 5],
    ids=["empty", "upper-case", "path", "too-long", "nul", "not-a-string"],
)
def test_a_name_no_project_can_have_is_one_line_and_stores_nothing(
    tmp_path: Path, project: object
) -> None:
    database = tmp_path / "vantage.db"
    reports = [{**report, "project": project} for report in _session()]

    message = _refusal(database, reports)

    assert message == (
        f"{database} refused the report of run {_RUN_ID}: "
        "The submitted report does not match the expected shape. (project)"
    )
    assert _projects(database) == [DEFAULT_PROJECT]
    store = SqliteExecutionStore(database)
    try:
        assert store.count_executions() == 0
    finally:
        store.close()


def test_a_run_named_again_in_another_project_is_one_line_and_stays_where_it_was(
    tmp_path: Path,
) -> None:
    """One session is one run in one project; a report of a stored run that
    names another project is not a way to move it."""
    database = tmp_path / "vantage.db"
    store_reports(database, _in_project("firmware"))

    message = _refusal(database, _in_project("boards"))

    assert message.startswith(f"cannot store run {_RUN_ID} in the database at {database}: ")
    assert _project_of(database, _RUN_ID) == "firmware"


def test_the_local_store_asks_nobodys_role_in_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody sends a report here but the database's owner, so `ingest` is
    handed no `admit`: a run lands in a project whose members, which a
    server gave the database, include nobody who could have sent it."""
    database = tmp_path / "vantage.db"
    server = SqliteExecutionStore(database)
    try:
        server.create_user("alice", admin=False, created_at=datetime.now(timezone.utc))
        server.create_project("firmware", created_at=datetime.now(timezone.utc))
        server.set_member("alice", project="firmware", role=VIEWER_ROLE)
    finally:
        server.close()
    admitted: list[object] = []

    def _ingest(*args: Any, **kwargs: Any) -> Ingested:
        admitted.append(kwargs.get("admit"))
        return ingest(*args, **kwargs)

    monkeypatch.setattr(vantage.local, "ingest", _ingest)

    store_reports(database, _in_project("firmware"))

    assert admitted == [None, None]
    assert _project_of(database, _RUN_ID) == "firmware"


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


def test_a_report_nested_deeper_than_the_encoder_recurses_is_refused(tmp_path: Path) -> None:
    # Deeper than any interpreter's recursion limit, Python's or C's.
    nested: list[Any] = []
    for _ in range(100_000):
        nested = [nested]
    report = {"run": _run(finished=True), "results": nested}

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


# --- Who made the database -----------------------------------------------------


def _origin(database: Path) -> str | None:
    """`meta.origin`, read with a plain connection of its own."""
    with contextlib.closing(sqlite3.connect(database)) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'origin'").fetchone()
    return None if row is None else str(row[0])


def test_a_database_the_local_store_creates_records_that_it_made_it(tmp_path: Path) -> None:
    database = tmp_path / "vantage.db"

    store_reports(database, _session())

    assert _origin(database) == "local"


def test_storing_into_a_database_a_server_made_leaves_it_the_servers(tmp_path: Path) -> None:
    """The default database is also the one `vantage` serves with no
    options. Once `vantage` has made it, local sessions store into it as
    before, and it stays the server's: an admin already guards it."""
    database = tmp_path / "vantage.db"
    SqliteExecutionStore(database).close()

    store_reports(database, _session())

    assert _origin(database) == "server"
    store = SqliteExecutionStore(database)
    try:
        assert store.get_execution(_RUN_ID) is not None
    finally:
        store.close()


@pytest.mark.parametrize(
    ("server_made_it", "users"), [(False, []), (True, ["admin"])], ids=["local", "server"]
)
def test_only_a_database_the_local_store_made_is_given_no_first_admin(
    tmp_path: Path, server_made_it: bool, users: list[str]
) -> None:
    """A test machine's own runs are its owner's: `vantage` serves the file
    the local store made open, as every database was served before users,
    until someone adds a user to it."""
    database = tmp_path / "vantage.db"
    if server_made_it:
        SqliteExecutionStore(database).close()
    store_reports(database, _session())

    store = SqliteExecutionStore(database)
    try:
        created = store.create_first_admin(
            "admin",
            password_hash=cheap_hash("a password nobody will type"),
            created_at=datetime.now(timezone.utc),
        )
        assert (created is not None) is server_made_it
        assert [user.name for user in store.list_users()] == users
        assert store.access_required() is server_made_it
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
