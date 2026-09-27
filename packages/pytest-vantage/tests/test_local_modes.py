"""The four modes -- `server`, `local`, `server+backup`, `server+local` --
from the plugin's side: which settings each reads and refuses, where each
sends and stores a run, what it queues, what it warns, and the queue sent
once a session reaches its server again.

The server side is a real `vantage` server. The local database is a
stand-in for `vantage.local`, installed by the conftest each test writes: it
keeps every call's reports, as JSON lines, in the file named as the
database. So a test sees exactly what the plugin handed local storage, and
can hand the same reports to a server to compare what each stores.
"""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_vantage.config import VantageConfigError, resolve_local_database, resolve_mode
from pytest_vantage.outbox import Outbox, outbox_path
from pytest_vantage.transport import send
from vantage.core.domain.execution import Execution
from vantage.service.errors import RejectionError
from vantage_test_server import ServerGate, VantageTestServer

_STAND_IN = """
import json
import sys
import types
from pathlib import Path

_stand_in = types.ModuleType("vantage.local")


class LocalStoreError(Exception):
    pass


def default_database_path():
    return Path(DEFAULT_PATH)


def store_reports(database, reports):
    if FAILURE is not None:
        raise LocalStoreError(FAILURE)
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps([dict(report) for report in reports]) + "\\n")


_stand_in.LocalStoreError = LocalStoreError
_stand_in.default_database_path = default_database_path
_stand_in.store_reports = store_reports
sys.modules["vantage.local"] = _stand_in
"""

_PASSING_TEST = "def test_it():\n    assert True\n"

_A_SESSION = """
import pytest


def test_passes(vantage_metadata):
    vantage_metadata["board"] = "rev-b"


def test_fails():
    print("to stdout")
    assert 1 == 2


@pytest.mark.skip(reason="not today")
def test_skipped():
    pass


@pytest.mark.parametrize("n", [1, 2])
def test_parametrised(n):
    assert n
"""


def _stand_in(
    pytester: pytest.Pytester, *, default: Path | None = None, failure: str | None = None
) -> Path:
    """Install the stand-in for the session under test and return the
    default database path it reports."""
    default = default or pytester.path / "data" / "vantage.db"
    pytester.makeconftest(
        _STAND_IN.replace("DEFAULT_PATH", repr(str(default))).replace("FAILURE", repr(failure))
    )
    return default


def _stored_sessions(database: Path) -> list[list[dict[str, Any]]]:
    """Every `store_reports` call's reports, in call order."""
    if not database.exists():
        return []
    return [json.loads(line) for line in database.read_text(encoding="utf-8").splitlines()]


def _queued(database: Path) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """The outbox's entries, oldest first: server, run id and reports."""
    path = outbox_path(database)
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT server, run_id, reports FROM entry ORDER BY id").fetchall()
    finally:
        conn.close()
    return [(server, run_id, json.loads(reports)) for server, run_id, reports in rows]


def _output(result: pytest.RunResult) -> str:
    return result.stdout.str() + result.stderr.str()


def _run_id_of(reports: list[dict[str, Any]]) -> str:
    run_id: str = reports[-1]["run"]["id"]
    return run_id


def _closed_port_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def second_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VantageTestServer]:
    server = VantageTestServer(tmp_path_factory.mktemp("second-server"))
    try:
        server.start()
        yield server
    finally:
        server.close()


class _UnprocessableError(RejectionError):
    status_code = 422
    error = "invalid_report"


class _UnavailableError(RejectionError):
    status_code = 503
    error = "unavailable"


def _fail_finishing_reports(
    server: VantageTestServer, monkeypatch: pytest.MonkeyPatch, failure: Exception | float
) -> None:
    """Make `server` refuse, or with a number of seconds stall, every report
    that finishes a run; start reports still land."""
    real = server.store.record_session

    def _record_session(execution: Execution, **kwargs: Any) -> bool:
        if execution.exit_status is not None:
            if isinstance(failure, Exception):
                raise failure
            time.sleep(failure)
        return real(execution, **kwargs)

    monkeypatch.setattr(server.store, "record_session", _record_session)


# --- Settings ----------------------------------------------------------------------


class _ConfigDouble:
    def __init__(self, options: dict[str, object], ini: dict[str, object]) -> None:
        self._options = options
        self._ini = ini

    def getoption(self, name: str, default: object = None) -> object:
        return self._options.get(name, default)

    def getini(self, name: str) -> object:
        return self._ini.get(name)


@pytest.mark.parametrize(
    ("options", "ini", "expected"),
    [
        ({"vantage_mode": "local"}, {"vantage_mode": "server+local"}, "local"),
        ({}, {"vantage_mode": "server+backup"}, "server+backup"),
        ({}, {"vantage_mode": ""}, "server"),
        ({}, {}, "server"),
    ],
)
def test_the_mode_is_the_command_line_then_the_ini_value_then_server(
    options: dict[str, object], ini: dict[str, object], expected: str
) -> None:
    assert resolve_mode(_ConfigDouble(options, ini)) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("options", "ini", "option"),
    [
        ({"vantage_mode": "offline"}, {}, "--vantage-mode"),
        ({}, {"vantage_mode": "Local"}, "vantage_mode"),
        ({}, {"vantage_mode": ["local"]}, "vantage_mode"),
    ],
)
def test_an_unknown_mode_is_refused_naming_its_source(
    options: dict[str, object], ini: dict[str, object], option: str
) -> None:
    with pytest.raises(VantageConfigError, match=f"{option}.*server, local"):
        resolve_mode(_ConfigDouble(options, ini))  # type: ignore[arg-type]


def test_the_local_database_is_the_command_line_then_the_ini_value_then_the_default(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative path is taken from where pytest was started when typed,
    and from the ini file's directory when committed there."""
    (pytester.path / "sub").mkdir()
    pytester.makeini("[pytest]\nvantage_local_database = stored/runs.db\n")
    monkeypatch.chdir(pytester.path / "sub")

    def default() -> Path:
        return Path("/default/vantage.db")

    typed = pytester.parseconfig(pytester.path, "--vantage-local-database=typed.db")
    committed = pytester.parseconfig(pytester.path)
    pytester.makeini("[pytest]\n")
    neither = pytester.parseconfig(pytester.path)

    assert resolve_local_database(typed, default=default) == pytester.path / "sub" / "typed.db"
    assert resolve_local_database(committed, default=default) == pytester.path / "stored/runs.db"
    assert resolve_local_database(neither, default=default) == Path("/default/vantage.db")


def test_an_override_with_no_ini_file_is_taken_from_where_pytest_was_started(
    pytester: pytest.Pytester,
) -> None:
    """As pytest takes its own path options: with no ini file to be relative
    to, a value given with `-o` is relative to where pytest was started, not
    to the rootdir."""
    (pytester.path / "sub").mkdir()
    config = pytester.parseconfig("--rootdir=sub", "-o", "vantage_local_database=runs.db")

    assert config.inipath is None
    assert config.rootpath == pytester.path / "sub"
    assert resolve_local_database(config, default=Path) == pytester.path / "runs.db"


def test_a_home_relative_local_database_is_expanded(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(pytester.path / "home"))
    config = pytester.parseconfig("--vantage-local-database=~/runs.db")

    assert resolve_local_database(config, default=Path) == pytester.path / "home" / "runs.db"


_INVALID_SETTINGS = {
    "postgresql url typed": (
        "vantage_mode = local",
        ["--vantage-local-database=postgresql://u:s3cret@db/v"],
        "ERROR: --vantage-local-database must be a SQLite file path, not a PostgreSQL URL*",
    ),
    "postgres url in the ini file": (
        "vantage_mode = local\nvantage_local_database = POSTGRES://u:s3cret@db/v",
        [],
        "ERROR: vantage_local_database ini value must be a SQLite file path*",
    ),
    "another url scheme": (
        "vantage_mode = local",
        ["--vantage-local-database=postgresql+psycopg://u:s3cret@db/v"],
        "ERROR: --vantage-local-database must be a SQLite file path, not a "
        "postgresql+psycopg:// URL",
    ),
    "a directory": (
        "vantage_mode = local",
        ["--vantage-local-database=."],
        "ERROR: --vantage-local-database * is a directory, not a database file",
    ),
    "an unknown mode": (
        "vantage_mode = everywhere",
        [],
        "ERROR: vantage_mode ini value must be one of server, local, *",
    ),
}


@pytest.mark.parametrize(
    ("ini_lines", "args", "error"),
    list(_INVALID_SETTINGS.values()),
    ids=list(_INVALID_SETTINGS),
)
def test_an_invalid_mode_or_local_database_is_a_usage_error_naming_it(
    pytester: pytest.Pytester, ini_lines: str, args: list[str], error: str
) -> None:
    """Local storage is SQLite only. A URL is never repeated back, since it
    may carry a password."""
    _stand_in(pytester)
    pytester.makeini(f"[pytest]\n{ini_lines}\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess("--vantage", *args)

    output = _output(result)
    assert result.ret == pytest.ExitCode.USAGE_ERROR, output
    assert "INTERNALERROR" not in output
    assert "s3cret" not in output
    result.stderr.fnmatch_lines([error])
    assert not list(pytester.path.glob("postgres*")), "a URL was taken as a path"


@pytest.mark.parametrize("mode", ["local", "server+backup", "server+local"])
@pytest.mark.parametrize("missing", ["vantage", "vantage.local"])
def test_a_mode_that_stores_locally_needs_the_vantage_package(
    pytester: pytest.Pytester, mode: str, missing: str
) -> None:
    pytester.makeconftest(f"import sys\nsys.modules[{missing!r}] = None\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-mode={mode}", f"--vantage-server={_closed_port_address()}"
    )

    assert result.ret == pytest.ExitCode.USAGE_ERROR, _output(result)
    # Matched anywhere: a terminal library may print its own notice first
    # (`No entry for terminal type`) when TERM is unset, as under sudo.
    assert (
        f"ERROR: vantage: --vantage-mode {mode} stores runs locally and needs the vantage "
        "package: pip install vantage"
    ) in result.stderr.lines


def test_server_mode_never_needs_the_vantage_package_or_reads_the_local_database(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    pytester.makeconftest("import sys\nsys.modules['vantage'] = None\n")
    pytester.makeini("[pytest]\nvantage_local_database = postgresql://u:s3cret@db/v\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=1, warnings=0)
    assert len(vantage_server.executions()) == 1


def test_the_mode_and_local_database_are_never_read_without_vantage(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeini(
        "[pytest]\nvantage_mode = everywhere\nvantage_local_database = postgresql://db/v\n"
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest()

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, warnings=0)


# --- local ------------------------------------------------------------------------


def test_local_mode_stores_the_run_and_opens_no_connection(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No preflight, no probe, no start report, no heartbeat: nothing ever
    connects to the address, and an address that could never work is not
    even read."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.5)
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    monkeypatch.setenv("VANTAGE_SERVER", "ftp://not-a-server")
    database = pytester.path / "runs" / "local.db"
    _stand_in(pytester)
    pytester.makeini(f"[pytest]\nvantage_server = {address}\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    try:
        result = pytester.runpytest_subprocess(
            "--vantage", "--vantage-mode=local", f"--vantage-local-database={database}"
        )
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()

    result.assert_outcomes(passed=1, warnings=0)
    ((report,),) = _stored_sessions(database)
    assert report["run"]["exit_status"] == 0
    assert report["run"]["finished_at"] is not None
    assert [result["node_id"] for result in report["results"]] == ["test_sample.py::test_it"]
    result.stdout.fnmatch_lines(
        [f"vantage: recording run {report['run']['id']} in project default to {database}"]
    )
    assert not outbox_path(database).exists()


def test_local_mode_stores_in_the_vantage_commands_default_database(
    pytester: pytest.Pytester,
) -> None:
    """So `vantage` started with no options serves what the tests stored."""
    default = _stand_in(pytester, default=pytester.path / "xdg" / "vantage" / "vantage.db")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess("--vantage", "--vantage-mode=local")

    result.assert_outcomes(passed=1)
    assert len(_stored_sessions(default)) == 1


def test_a_local_mode_is_honoured_from_the_ini_file(pytester: pytest.Pytester) -> None:
    """Like the address, the mode says where, never whether."""
    database = _stand_in(pytester)
    pytester.makeini("[pytest]\nvantage_mode = local\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    pytester.runpytest_subprocess("--vantage").assert_outcomes(passed=1)
    pytester.runpytest_subprocess().assert_outcomes(passed=1)

    assert len(_stored_sessions(database)) == 1


@pytest.mark.parametrize(("test_body", "exit_status"), [("assert True", 0), ("assert False", 1)])
def test_a_failed_local_store_in_local_mode_warns_that_the_run_is_lost(
    pytester: pytest.Pytester, test_body: str, exit_status: int
) -> None:
    database = _stand_in(pytester, failure="disk is full")
    pytester.makepyfile(test_sample=f"def test_it():\n    {test_body}\n")

    result = pytester.runpytest_subprocess("--vantage", "--vantage-mode=local")

    assert result.ret == exit_status
    output = _output(result)
    assert output.count("VantageWarning:") == 1
    assert (
        f"vantage: could not store this run in {database}: disk is full; the run is lost" in output
    )


# --- server+local -------------------------------------------------------------------


def test_server_plus_local_sends_and_stores_the_same_run(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    second_server: VantageTestServer,
) -> None:
    """The local copy is the reports the server took, so the same reports
    handed to another server are stored exactly as the first stored them."""
    database = _stand_in(pytester)
    pytester.makepyfile(test_session=_A_SESSION)

    result = pytester.runpytest_subprocess(
        "--vantage",
        "--vantage-failure-text",
        "--vantage-mode=server+local",
        f"--vantage-server={vantage_server.address}",
    )

    result.assert_outcomes(passed=3, failed=1, skipped=1, warnings=0)
    (stored,) = _stored_sessions(database)
    run_id = _run_id_of(stored)
    result.stdout.fnmatch_lines(
        [
            f"vantage: recording run {run_id} in project default "
            f"to {vantage_server.address} and {database}"
        ]
    )
    for report in stored:
        send(second_server.address, report, timeout=5.0)
    _assert_same_run(vantage_server, second_server, run_id)
    assert _queued(database) == []


def _assert_same_run(first: VantageTestServer, second: VantageTestServer, run_id: str) -> None:
    (execution,) = [e for e in first.executions() if e.identity.value == run_id]
    assert execution.exit_status is not None
    assert [e for e in second.executions() if e.identity.value == run_id] == [execution]
    assert sorted(first.store.get_results(run_id), key=repr) == sorted(
        second.store.get_results(run_id), key=repr
    )
    assert first.store.get_results(run_id)
    first_metadata, second_metadata = first.metadata(run_id), second.metadata(run_id)
    assert set(first_metadata.entries) == set(second_metadata.entries)
    assert set(first_metadata.files) == set(second_metadata.files)
    assert any(entry.value == "rev-b" for entry in first_metadata.entries)


def test_a_failed_local_store_in_server_plus_local_warns_and_the_server_has_the_run(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    database = _stand_in(pytester, failure="unable to open database file")
    pytester.makepyfile(test_sample="def test_it():\n    assert False\n")

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+local", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == 1
    output = _output(result)
    assert output.count("VantageWarning:") == 1
    assert f"vantage: could not store this run in {database}: unable to open database" in output
    (execution,) = vantage_server.executions()
    assert execution.exit_status == 1


# --- A server that cannot take the run ---------------------------------------------------


@pytest.mark.parametrize("mode", ["server+backup", "server+local"])
def test_a_server_unreachable_at_the_start_leaves_the_run_stored_and_queued(
    pytester: pytest.Pytester, mode: str
) -> None:
    address = _closed_port_address()
    database = _stand_in(pytester)
    pytester.makepyfile(test_session=_A_SESSION)

    results = [
        pytester.runpytest_subprocess(
            "--vantage", f"--vantage-mode={mode}", f"--vantage-server={address}"
        )
        for _ in range(2)
    ]

    for index, result in enumerate(results, start=1):
        assert result.ret == 1
        output = _output(result)
        assert output.count("VantageWarning:") == 1, output
        waiting = "1 run" if index == 1 else "2 runs"
        assert (
            f"vantage: {address} is unreachable; this run was stored in {database} "
            f"and queued ({waiting} waiting to be sent)"
        ) in output
    sessions = _stored_sessions(database)
    assert len(sessions) == 2
    queued = _queued(database)
    assert [(server, run_id) for server, run_id, _ in queued] == [
        (address, _run_id_of(stored)) for stored in sessions
    ]
    # Everything the session reports at the finish, nothing it never sent.
    assert [reports for _, _, reports in queued] == sessions


def test_the_next_session_that_reaches_the_server_sends_the_queue(
    pytester: pytest.Pytester,
    server_gate: ServerGate,
    vantage_server: VantageTestServer,
    second_server: VantageTestServer,
) -> None:
    """The run queued while the server was down reaches it complete --
    results and metadata -- exactly as the same reports sent directly, and
    leaves the outbox."""
    database = _stand_in(pytester)
    pytester.makepyfile(test_session=_A_SESSION)
    args = ("--vantage", "--vantage-failure-text", "--vantage-mode=server+backup")

    down = pytester.runpytest_subprocess(*args, f"--vantage-server={server_gate.address}")
    server_gate.open(vantage_server)
    back = pytester.runpytest_subprocess(*args, f"--vantage-server={server_gate.address}")

    assert "is unreachable" in _output(down)
    (queued_run,) = _stored_sessions(database)
    queued_id = _run_id_of(queued_run)
    back.stdout.fnmatch_lines([f"vantage: sent 1 queued run to {server_gate.address} (0 waiting)"])
    assert _output(back).count("VantageWarning:") == 0
    assert _queued(database) == []
    run_ids = [execution.identity.value for execution in vantage_server.executions()]
    assert len(run_ids) == 2
    assert queued_id in run_ids
    for report in queued_run:
        send(second_server.address, report, timeout=5.0)
    _assert_same_run(vantage_server, second_server, queued_id)


def test_a_5xx_at_the_finish_leaves_the_run_stored_and_queued(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_finishing_reports(vantage_server, monkeypatch, _UnavailableError("storage is down"))
    database = _stand_in(pytester)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == 0
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert f"vantage: {vantage_server.address} did not take this run (HTTP Error 503: " in output
    assert f"this run was stored in {database} and queued (1 run waiting to be sent)" in output
    (stored,) = _stored_sessions(database)
    ((server, run_id, reports),) = _queued(database)
    assert (server, run_id, reports) == (vantage_server.address, _run_id_of(stored), stored)


class _RetryLaterError(RejectionError):
    """What a proxy in front of the server answers when it is busy."""

    error = "retry_later"

    def __init__(self, status_code: int) -> None:
        super().__init__("try again later")
        self.status_code = status_code


@pytest.mark.parametrize("status", [408, 429])
def test_a_408_or_429_at_the_finish_leaves_the_run_stored_and_queued(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    """Unlike any other 4xx, both ask for the same request again later."""
    _fail_finishing_reports(vantage_server, monkeypatch, _RetryLaterError(status))
    database = _stand_in(pytester)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == 0
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert (
        f"vantage: {vantage_server.address} did not take this run (HTTP Error {status}: " in output
    )
    assert f"this run was stored in {database} and queued (1 run waiting to be sent)" in output
    (stored,) = _stored_sessions(database)
    ((server, run_id, reports),) = _queued(database)
    assert (server, run_id, reports) == (vantage_server.address, _run_id_of(stored), stored)


def test_a_server_gone_by_the_finish_leaves_the_run_stored_and_queued(
    pytester: pytest.Pytester, server_gate: ServerGate, vantage_server: VantageTestServer
) -> None:
    """It answered the preflight, the probe and the start report, and then
    went away before the run's finish report."""
    server_gate.open(vantage_server, connections=3)
    database = _stand_in(pytester)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={server_gate.address}"
    )

    assert result.ret == 0
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert f"vantage: {server_gate.address} is unreachable (" in output
    assert f"this run was stored in {database} and queued (1 run waiting to be sent)" in output
    (execution,) = vantage_server.executions()
    assert execution.exit_status is None
    (stored,) = _stored_sessions(database)
    ((server, run_id, reports),) = _queued(database)
    assert (server, run_id, reports) == (server_gate.address, execution.identity.value, stored)


def test_only_the_reports_the_server_did_not_take_are_queued(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session too large for one report sends its results in several. The
    ones the server took stay taken; the queue holds the rest, and sending
    it later finishes the run with every result."""
    failing = [True]
    real = vantage_server.store.record_session

    def _record_session(execution: Execution, **kwargs: Any) -> bool:
        if failing[0] and execution.exit_status is not None:
            raise _UnavailableError("storage is down")
        return real(execution, **kwargs)

    monkeypatch.setattr(vantage_server.store, "record_session", _record_session)
    database = _stand_in(pytester)
    pytester.makepyfile(
        test_large="import pytest\n\n\n"
        "@pytest.mark.parametrize('n', range(300), ids=lambda n: f'{n:04d}-' + 'x' * 2000)\n"
        "def test_p(n):\n    assert True\n",
        test_small=_PASSING_TEST,
    )
    args = (
        "--vantage",
        "--vantage-mode=server+backup",
        f"--vantage-server={vantage_server.address}",
    )

    pytester.runpytest_subprocess(*args, "test_large.py").assert_outcomes(passed=300)
    (stored,) = _stored_sessions(database)
    ((_, run_id, queued),) = _queued(database)
    failing[0] = False
    later = pytester.runpytest_subprocess(*args, "test_small.py")

    assert len(stored) > 1
    assert queued == stored[-1:]
    later.stdout.fnmatch_lines([f"vantage: sent 1 queued run to {vantage_server.address} *"])
    assert _queued(database) == []
    (execution,) = [e for e in vantage_server.executions() if e.identity.value == run_id]
    assert execution.exit_status == 0
    assert len(vantage_server.store.get_results(run_id)) == 300


@pytest.mark.slow
def test_a_timeout_at_the_finish_leaves_the_run_queued(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server may store it after all; sent again, it is stored once."""
    _fail_finishing_reports(vantage_server, monkeypatch, 2.5)
    database = _stand_in(pytester)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage",
        "--vantage-mode=server+local",
        "--vantage-timeout=1",
        f"--vantage-server={vantage_server.address}",
    )

    assert result.ret == 0
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert "no complete answer within 1s" in output
    assert "and queued (1 run waiting to be sent)" in output
    assert len(_queued(database)) == 1


def test_a_4xx_at_the_finish_is_not_queued_but_the_backup_stores_the_run(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sending it again would be refused the same way."""
    _fail_finishing_reports(vantage_server, monkeypatch, _UnprocessableError("refused"))
    database = _stand_in(pytester)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == 0
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert "vantage: error while reporting: HTTP Error 422" in output
    assert f"this run was stored in {database} only" in output
    assert len(_stored_sessions(database)) == 1
    assert _queued(database) == []


@pytest.mark.parametrize(("test_body", "exit_status"), [("assert True", 0), ("assert False", 1)])
def test_server_mode_with_a_project_the_server_lacks_warns_and_leaves_the_exit_status(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, test_body: str, exit_status: int
) -> None:
    """The server refuses the start and the finish for want of the
    project; each path warns once, naming the command that fixes it, and
    the suite ends as it would have without the plugin. Server mode keeps
    nothing, so the run is not stored or queued anywhere."""
    database = _stand_in(pytester)
    pytester.makepyfile(test_sample=f"def test_it():\n    {test_body}\n")

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-project=firmware", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == exit_status
    result.stdout.fnmatch_lines(
        [f"vantage: recording run * in project firmware to {vantage_server.address}"]
    )
    output = _output(result)
    refusal = (
        f"HTTP 404: {vantage_server.address} has no project firmware; "
        "an admin adds it with: vantage project add firmware"
    )
    assert output.count("VantageWarning:") == 2, output
    assert f"vantage: error while reporting session liveness: {refusal}\n" in output
    assert f"vantage: error while reporting: {refusal}\n" in output
    assert vantage_server.executions() == []
    assert vantage_server.requests.count(("POST", "/api/v1/runs")) == 2
    assert not database.exists()
    assert not outbox_path(database).exists()


def test_server_plus_backup_stores_nothing_locally_when_the_server_takes_the_run(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    database = _stand_in(pytester)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=1, warnings=0)
    result.stdout.fnmatch_lines(
        [f"vantage: recording run * in project default to {vantage_server.address}"]
    )
    assert len(vantage_server.executions()) == 1
    assert not database.exists()
    assert not outbox_path(database).exists()


# --- Sending the queue -------------------------------------------------------------------


def _queue_directly(database: Path, server: str, run_id: str, *, valid: bool = True) -> None:
    started = "2026-01-02T03:04:05.000000+00:00"
    run = {
        "id": run_id,
        "started_at": started,
        "finished_at": started,
        "exit_status": 0 if valid else "not a status",
        "interrupted": False,
        "interrupt_reason": None,
    }
    with Outbox(outbox_path(database)) as box:
        box.enqueue(server, run_id, [{"run": run, "results": []}])


def test_a_queued_run_the_server_rejects_is_dropped_with_a_warning_naming_it(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    database = _stand_in(pytester)
    _queue_directly(database, vantage_server.address, "a" * 32, valid=False)
    _queue_directly(database, vantage_server.address, "b" * 32)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={vantage_server.address}"
    )

    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert f"rejected queued run {'a' * 32}, which can never be sent" in output
    result.stdout.fnmatch_lines([f"vantage: sent 1 queued run to {vantage_server.address} *"])
    assert _queued(database) == []
    assert {"b" * 32} < {execution.identity.value for execution in vantage_server.executions()}


def test_a_queued_run_that_cannot_be_read_back_is_dropped_with_a_warning_naming_it(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    database = _stand_in(pytester)
    _queue_directly(database, vantage_server.address, "a" * 32)
    _queue_directly(database, vantage_server.address, "b" * 32)
    conn = sqlite3.connect(outbox_path(database))
    with conn:
        conn.execute("UPDATE entry SET reports = '[{' WHERE run_id = ?", ("a" * 32,))
    conn.close()
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == 0
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert (
        f"vantage: queued run {'a' * 32} could not be read back from {outbox_path(database)}; "
        "it was dropped" in output
    )
    result.stdout.fnmatch_lines([f"vantage: sent 1 queued run to {vantage_server.address} *"])
    assert _queued(database) == []


def test_runs_queued_for_another_server_are_left_alone(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    database = _stand_in(pytester)
    _queue_directly(database, "http://ci-vantage:8765", "c" * 32)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+local", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=1, warnings=0)
    assert "queued run" not in _output(result)
    assert [(server, run_id) for server, run_id, _ in _queued(database)] == [
        ("http://ci-vantage:8765", "c" * 32)
    ]


def test_the_queue_is_never_sent_to_a_server_that_did_not_take_this_sessions_run(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_finishing_reports(vantage_server, monkeypatch, _UnavailableError("storage is down"))
    database = _stand_in(pytester)
    _queue_directly(database, vantage_server.address, "d" * 32)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={vantage_server.address}"
    )

    assert [run_id for _, run_id, _ in _queued(database)][0] == "d" * 32
    assert len(_queued(database)) == 2
    assert vantage_server.requests == [
        ("GET", "/api/v1/capabilities"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/runs"),
    ]


def test_a_full_outbox_drops_its_oldest_run_with_a_warning_naming_it(
    pytester: pytest.Pytester,
) -> None:
    address = _closed_port_address()
    database = _stand_in(pytester)
    _queue_directly(database, address, "e" * 32)
    conftest = pytester.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + "\nimport pytest_vantage.outbox\npytest_vantage.outbox.MAX_ENTRIES = 1\n"
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={address}"
    )

    output = _output(result)
    assert output.count("VantageWarning:") == 2, output
    assert f"dropped the oldest queued run to make room: {'e' * 32}" in output
    assert "queued (1 run waiting to be sent)" in output
    ((_, run_id, _),) = _queued(database)
    assert run_id != "e" * 32


def test_two_sessions_sending_the_same_queue_at_once_deliver_each_run_once(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    database = _stand_in(pytester)
    queued = [f"{index:032x}" for index in range(12)]
    for run_id in queued:
        _queue_directly(database, vantage_server.address, run_id)
    pytester.makepyfile(test_sample=_PASSING_TEST)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--vantage",
        "--vantage-mode=server+backup",
        f"--vantage-server={vantage_server.address}",
    ]

    sessions = [
        pytester.popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
        )
        for _ in range(2)
    ]
    outputs = [session.communicate(timeout=60)[0].decode() for session in sessions]

    assert [session.returncode for session in sessions] == [0, 0]
    sent = [int(match) for output in outputs for match in re.findall(r"sent (\d+) queued", output)]
    assert sum(sent) == len(queued)
    assert _queued(database) == []
    stored = [execution.identity.value for execution in vantage_server.executions()]
    assert len(stored) == len(queued) + 2
    assert set(queued) < set(stored)
    # Two reports for each session's own run, one for each queued run.
    assert len(vantage_server.requests) == 2 * 3 + len(queued)


# --- xdist -------------------------------------------------------------------------------

_FOUR_TESTS = "\n\n".join(f"def test_{index}():\n    assert True\n" for index in range(4))


@pytest.mark.parametrize(
    ("mode", "on_server", "stored_locally"),
    [("local", 0, 1), ("server+local", 1, 1), ("server+backup", 1, 0)],
)
def test_under_xdist_only_the_controller_stores_the_run(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    mode: str,
    on_server: int,
    stored_locally: int,
) -> None:
    pytest.importorskip("xdist")
    database = _stand_in(pytester)
    pytester.makepyfile(test_four=_FOUR_TESTS)

    result = pytester.runpytest_subprocess(
        "--vantage",
        f"--vantage-mode={mode}",
        f"--vantage-server={vantage_server.address}",
        "-n",
        "2",
    )

    result.assert_outcomes(passed=4)
    assert len(vantage_server.executions()) == on_server
    sessions = _stored_sessions(database)
    assert len(sessions) == stored_locally
    for session in sessions:
        results = [result for report in session for result in report["results"]]
        assert {result["worker_id"] for result in results} == {"gw0", "gw1"}
    assert _queued(database) == []


@pytest.mark.parametrize("verbosity", [[], ["-v"], ["-q"]])
def test_under_xdist_the_queue_summary_is_a_line_of_its_own(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, verbosity: list[str]
) -> None:
    """xdist leaves the progress line open until the terminal summary, so a
    line written as the session finishes would be glued to its end."""
    pytest.importorskip("xdist")
    database = _stand_in(pytester)
    _queue_directly(database, vantage_server.address, "c" * 32)
    pytester.makepyfile(test_four=_FOUR_TESTS)

    result = pytester.runpytest_subprocess(
        "--vantage",
        "--vantage-mode=server+backup",
        f"--vantage-server={vantage_server.address}",
        "-n",
        "2",
        *verbosity,
    )

    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines([f"vantage: sent 1 queued run to {vantage_server.address} *"])


@pytest.mark.parametrize("hidden", [["--no-summary"], ["-p", "no:terminal"]])
def test_the_queue_summary_is_printed_without_a_terminal_summary(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, hidden: list[str]
) -> None:
    database = _stand_in(pytester)
    _queue_directly(database, vantage_server.address, "d" * 32)
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest_subprocess(
        "--vantage",
        "--vantage-mode=server+backup",
        f"--vantage-server={vantage_server.address}",
        *hidden,
    )

    assert result.ret == 0
    assert _output(result).count(f"vantage: sent 1 queued run to {vantage_server.address} ") == 1
    assert _queued(database) == []


def test_under_xdist_an_unreachable_server_queues_the_run_once(pytester: pytest.Pytester) -> None:
    pytest.importorskip("xdist")
    address = _closed_port_address()
    database = _stand_in(pytester)
    pytester.makepyfile(test_four=_FOUR_TESTS)

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=server+backup", f"--vantage-server={address}", "-n", "2"
    )

    result.assert_outcomes(passed=4)
    assert len(_stored_sessions(database)) == 1
    ((_, _, reports),) = _queued(database)
    assert len([result for report in reports for result in report["results"]]) == 4
