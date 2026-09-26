"""The local modes end to end: real pytest sessions, a real `vantage`
server, and the real SQLite database `vantage.local` writes, served
afterwards by a `vantage` app of its own, as `vantage` started on the test
machine would serve it.

`test_local_modes.py` checks what the plugin hands local storage, through a
stand-in; this module checks what ends up stored, and that a run reads back
the same through the read API wherever it went: sent directly, stored
locally, or queued and sent later by a session or by `vantage push`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.parse import quote

import pytest
from pytest_vantage.outbox import Outbox, outbox_path
from vantage.core.domain.execution import Execution
from vantage.ingestion.errors import RejectionError
from vantage_test_server import ServerGate, VantageTestServer

pytestmark = pytest.mark.usefixtures("git_confined_to_basetemp")

_SESSION = """
import pytest


def test_passes(vantage_metadata):
    vantage_metadata["board"] = "rev-b"


def test_fails():
    print("to stdout")
    assert 1 == 2


@pytest.mark.skip(reason="not today")
def test_skipped():
    pass


@pytest.mark.xfail(reason="known")
def test_known():
    assert False


@pytest.mark.parametrize("n", [1, 2])
def test_parametrised(n):
    assert n
"""

_NODE_IDS = 6

_DECLARATION = {
    "version": 1,
    "files": [{"path": "settings.json", "format": "json", "keys": ["service_version"]}],
    "keys": {"board": {"name": "Board revision"}},
}

_RECORD = ("--vantage", "--vantage-failure-text", "--vantage-metadata")

# What differs between two sessions of the same tests: when they ran and
# for how long.
_TIMING = frozenset(
    {
        "started_at",
        "finished_at",
        "duration",
        "setup_duration",
        "call_duration",
        "teardown_duration",
    }
)

# Talks to the loopback servers directly, whatever proxy the environment names.
_OPENER = urllib_request.build_opener(urllib_request.ProxyHandler({}))


def _write_session(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_session=_SESSION)
    (pytester.path / "vantage-metadata.json").write_text(json.dumps(_DECLARATION))
    (pytester.path / "settings.json").write_text('{"service_version": "2.10"}')


def _get(address: str, path: str) -> Any:
    with _OPENER.open(address + path, timeout=10) as response:
        return json.loads(response.read())


def _read_back(address: str, run_id: str) -> dict[str, Any]:
    """Everything the read API says about one run."""
    results = _get(address, f"/api/v1/runs/{run_id}/results?limit=200")
    assert not results["has_more"]
    return {
        "run": _get(address, f"/api/v1/runs/{run_id}"),
        "results": results["items"],
        "details": [
            _get(address, f"/api/v1/runs/{run_id}/result?node_id={quote(item['node_id'])}")
            for item in results["items"]
        ],
        "metadata": _get(address, f"/api/v1/runs/{run_id}/metadata")["items"],
    }


def _untimed(read_back: dict[str, Any]) -> dict[str, Any]:
    """A run's read-back without its id and timings: what two sessions of
    the same tests must share."""
    run = {key: value for key, value in read_back["run"].items() if key not in {"id", *_TIMING}}
    return {
        "run": run,
        "results": [_without_timing(item) for item in read_back["results"]],
        "details": [_without_timing(item) for item in read_back["details"]],
        "metadata": read_back["metadata"],
    }


def _without_timing(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in _TIMING}


def _run_ids(address: str) -> list[str]:
    return [item["id"] for item in _get(address, "/api/v1/runs?limit=200")["items"]]


def _recorded_run(result: pytest.RunResult) -> str:
    (match,) = re.findall(r"^vantage: recording run ([0-9a-f]{32}) to ", result.stdout.str(), re.M)
    run_id: str = match
    return run_id


def _output(result: pytest.RunResult) -> str:
    return result.stdout.str() + result.stderr.str()


def _waiting(database: Path) -> int:
    with Outbox(outbox_path(database)) as outbox:
        return outbox.waiting()


@pytest.fixture
def serve_local() -> Iterator[Any]:
    """Serves a local database with a `vantage` app of its own, as
    `vantage --database <it>` would; started only once the sessions under
    test are done with the file."""
    servers: list[VantageTestServer] = []

    def _serve(database: Path) -> VantageTestServer:
        # The harness serves `<directory>/vantage.db`.
        assert database.name == "vantage.db"
        assert database.exists()
        server = VantageTestServer(database.parent)
        servers.append(server)
        server.start()
        return server

    try:
        yield _serve
    finally:
        for server in servers:
            server.close()


class _UnprocessableError(RejectionError):
    status_code = 422
    error = "invalid_report"


# --- server+backup: the whole story -----------------------------------------------------


def test_a_backed_up_run_reaches_the_server_once_it_is_back_as_if_sent_directly(
    pytester: pytest.Pytester,
    server_gate: ServerGate,
    vantage_server: VantageTestServer,
    serve_local: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Down at the start, then down by the finish: each run is stored in
    the default local database and queued, with one warning. The next
    session that reaches the server sends both. The server then holds every
    run once, the queued ones reading back exactly as the local database
    serves them and as the run it was sent directly."""
    data_home = pytester.path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    database = data_home / "vantage" / "vantage.db"
    _write_session(pytester)
    address = server_gate.address
    args = (*_RECORD, "--vantage-mode=server+backup", f"--vantage-server={address}")

    down_at_start = pytester.runpytest_subprocess(*args)
    # The preflight, the capability probe and the start report get through.
    server_gate.open(vantage_server, connections=3)
    down_at_finish = pytester.runpytest_subprocess(*args)
    server_gate.let_through(None)
    back = pytester.runpytest_subprocess(*args)

    queued_ids = [_recorded_run(down_at_start), _recorded_run(down_at_finish)]
    direct_id = _recorded_run(back)
    for result in (down_at_start, down_at_finish, back):
        result.assert_outcomes(passed=3, failed=1, skipped=1, xfailed=1)
    first, second = _output(down_at_start), _output(down_at_finish)
    assert first.count("VantageWarning:") == 1, first
    assert (
        f"vantage: {address} is unreachable; this run was stored in {database} "
        "and queued (1 run waiting to be sent)"
    ) in first
    assert second.count("VantageWarning:") == 1, second
    assert f"vantage: {address} is unreachable (" in second
    assert f"this run was stored in {database} and queued (2 runs waiting to be sent)" in second
    back.stdout.fnmatch_lines([f"vantage: sent 2 queued runs to {address} (0 waiting)"])
    assert "VantageWarning:" not in _output(back)
    assert _waiting(database) == 0

    central = vantage_server.address
    assert sorted(_run_ids(central)) == sorted([*queued_ids, direct_id])
    history = _get(central, "/api/v1/tests/history?node_id=test_session.py::test_fails")
    assert sorted(item["run_id"] for item in history["items"]) == sorted(_run_ids(central))
    # Only the runs the server could not take are kept locally.
    local = serve_local(database).address
    assert sorted(_run_ids(local)) == sorted(queued_ids)
    direct = _read_back(central, direct_id)
    assert len(direct["results"]) == _NODE_IDS
    assert {item["key"]: item["value"] for item in direct["metadata"]} == {
        "board": "rev-b",
        "service_version": "2.10",
    }
    for run_id in queued_ids:
        replayed = _read_back(central, run_id)
        assert replayed == _read_back(local, run_id)
        assert _untimed(replayed) == _untimed(direct)


def test_a_run_the_server_refuses_is_kept_locally_and_never_queued(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    serve_local: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4xx would be the same next time, so nothing is queued, but the
    local database still takes what the server would not."""
    real = vantage_server.store.record_session

    def _refuse_the_finish(execution: Execution, **kwargs: Any) -> bool:
        if execution.exit_status is not None:
            raise _UnprocessableError("refused")
        return real(execution, **kwargs)

    monkeypatch.setattr(vantage_server.store, "record_session", _refuse_the_finish)
    database = pytester.path / "local" / "vantage.db"
    _write_session(pytester)

    result = pytester.runpytest_subprocess(
        *_RECORD,
        "--vantage-mode=server+backup",
        f"--vantage-server={vantage_server.address}",
        f"--vantage-local-database={database}",
    )

    assert result.ret == 1
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert "vantage: error while reporting: HTTP Error 422" in output
    assert f"this run was stored in {database} only" in output
    assert not outbox_path(database).exists()
    run_id = _recorded_run(result)
    stored = _read_back(serve_local(database).address, run_id)
    assert stored["run"]["presentation"] == "finished"
    assert len(stored["results"]) == _NODE_IDS


# --- server+local and local ---------------------------------------------------------------


def test_server_plus_local_keeps_the_same_run_on_the_server_and_in_the_local_database(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, serve_local: Any
) -> None:
    database = pytester.path / "local" / "vantage.db"
    _write_session(pytester)

    result = pytester.runpytest_subprocess(
        *_RECORD,
        "--vantage-mode=server+local",
        f"--vantage-server={vantage_server.address}",
        f"--vantage-local-database={database}",
    )

    result.assert_outcomes(passed=3, failed=1, skipped=1, xfailed=1, warnings=0)
    run_id = _recorded_run(result)
    result.stdout.fnmatch_lines(
        [f"vantage: recording run {run_id} to {vantage_server.address} and {database}"]
    )
    on_server = _read_back(vantage_server.address, run_id)
    assert len(on_server["results"]) == _NODE_IDS
    assert on_server == _read_back(serve_local(database).address, run_id)
    assert not outbox_path(database).exists()


def test_local_mode_stores_where_vantage_serves_without_options_and_sends_nothing(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    serve_local: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run stored locally reads back as the same session sent to a
    server does, and the server configured alongside hears nothing."""
    data_home = pytester.path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    database = data_home / "vantage" / "vantage.db"
    pytester.makeini(f"[pytest]\nvantage_server = {vantage_server.address}\n")
    _write_session(pytester)

    stored = pytester.runpytest_subprocess(*_RECORD, "--vantage-mode=local")
    requests_while_local = list(vantage_server.requests)
    sent = pytester.runpytest_subprocess(*_RECORD)

    stored.assert_outcomes(passed=3, failed=1, skipped=1, xfailed=1, warnings=0)
    run_id = _recorded_run(stored)
    stored.stdout.fnmatch_lines([f"vantage: recording run {run_id} to {database}"])
    assert requests_while_local == []
    assert not outbox_path(database).exists()
    local = serve_local(database).address
    assert _run_ids(local) == [run_id]
    locally = _read_back(local, run_id)
    assert locally["run"]["presentation"] == "finished"
    assert _untimed(locally) == _untimed(_read_back(vantage_server.address, _recorded_run(sent)))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_a_new_local_database_and_its_outbox_are_owner_only(pytester: pytest.Pytester) -> None:
    closed = ServerGate()
    try:
        database = pytester.path / "private" / "vantage.db"
        pytester.makepyfile(test_it="def test_it():\n    assert True\n")

        pytester.runpytest_subprocess(
            "--vantage",
            "--vantage-mode=server+backup",
            f"--vantage-server={closed.address}",
            f"--vantage-local-database={database}",
        ).assert_outcomes(passed=1)
    finally:
        closed.close()

    assert database.parent.stat().st_mode & 0o777 == 0o700
    assert database.stat().st_mode & 0o777 == 0o600
    assert outbox_path(database).stat().st_mode & 0o777 == 0o600


# --- A local database that cannot take the run ------------------------------------------


def test_local_mode_leaves_a_database_of_another_schema_alone_and_only_warns(
    pytester: pytest.Pytester,
) -> None:
    database = pytester.path / "old" / "vantage.db"
    database.parent.mkdir()
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.commit()
    conn.close()
    before = database.read_bytes()
    pytester.makepyfile(test_it="def test_it():\n    assert True\n")

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-mode=local", f"--vantage-local-database={database}"
    )

    assert result.ret == 0
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert f"vantage: could not store this run in {database}: " in output
    assert "schema_version is 1" in output
    assert "the run is lost" in output
    assert database.read_bytes() == before


def test_server_plus_local_with_an_unusable_local_database_still_reports_to_the_server(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    blocker = pytester.path / "not-a-directory"
    blocker.write_text("")
    database = blocker / "vantage.db"
    pytester.makepyfile(test_it="def test_it():\n    assert False\n")

    result = pytester.runpytest_subprocess(
        "--vantage",
        "--vantage-mode=server+local",
        f"--vantage-server={vantage_server.address}",
        f"--vantage-local-database={database}",
    )

    assert result.ret == 1
    output = _output(result)
    assert output.count("VantageWarning:") == 1, output
    assert f"vantage: could not store this run in {database}: cannot open the database" in output
    (execution,) = vantage_server.executions()
    assert execution.exit_status == 1


# --- xdist ----------------------------------------------------------------------------------

_FOUR_TESTS = "\n\n".join(f"def test_{index}():\n    assert True\n" for index in range(4))


@pytest.mark.parametrize("mode", ["local", "server+local", "server+backup"])
def test_under_xdist_the_run_is_stored_locally_once_with_every_workers_results(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, serve_local: Any, mode: str
) -> None:
    """`server+backup` against a server that is down, so it too stores."""
    pytest.importorskip("xdist")
    closed = ServerGate()
    address = closed.address if mode == "server+backup" else vantage_server.address
    database = pytester.path / "local" / "vantage.db"
    pytester.makepyfile(test_four=_FOUR_TESTS)

    try:
        result = pytester.runpytest_subprocess(
            "--vantage",
            f"--vantage-mode={mode}",
            f"--vantage-server={address}",
            f"--vantage-local-database={database}",
            "-n",
            "2",
        )
    finally:
        closed.close()

    result.assert_outcomes(passed=4)
    run_id = _recorded_run(result)
    local = serve_local(database).address
    assert _run_ids(local) == [run_id]
    stored = _read_back(local, run_id)
    assert {item["worker_id"] for item in stored["results"]} == {"gw0", "gw1"}
    assert len(stored["results"]) == 4
    if mode == "server+local":
        assert _read_back(vantage_server.address, run_id) == stored
    else:
        assert vantage_server.executions() == []
    if mode == "server+backup":
        assert _waiting(database) == 1
    else:
        assert not outbox_path(database).exists()


# --- vantage push -----------------------------------------------------------------------------


def _push(*argv: str) -> subprocess.CompletedProcess[str]:
    """`vantage push`, in a process of its own, as a user runs it."""
    return subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", "from vantage.service.cli import main; main()", "push", *argv],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_vantage_push_sends_a_queued_run_once_its_server_is_back(
    pytester: pytest.Pytester,
    server_gate: ServerGate,
    vantage_server: VantageTestServer,
    serve_local: Any,
) -> None:
    database = pytester.path / "local" / "vantage.db"
    address = server_gate.address
    _write_session(pytester)
    session = pytester.runpytest_subprocess(
        *_RECORD,
        "--vantage-mode=server+backup",
        f"--vantage-server={address}",
        f"--vantage-local-database={database}",
    )
    run_id = _recorded_run(session)
    assert "and queued (1 run waiting to be sent)" in _output(session)

    still_down = _push("--database", str(database))
    server_gate.open(vantage_server)
    elsewhere = _push("--database", str(database), "--to", "http://ci-vantage:8765")
    sent = _push("--database", str(database))
    again = _push("--database", str(database))

    assert still_down.returncode == 1, still_down.stderr
    assert still_down.stdout.startswith(
        f"vantage: sent 0 queued runs to {address}, then stopped: {address} is unreachable ("
    )
    assert still_down.stdout.endswith(" (1 waiting)\n")
    assert (elsewhere.returncode, elsewhere.stdout) == (
        0,
        "vantage: nothing queued for http://ci-vantage:8765\n",
    )
    assert (sent.returncode, sent.stdout, sent.stderr) == (
        0,
        f"vantage: sent 1 queued run to {address} (0 waiting)\n",
        "",
    )
    assert (again.returncode, again.stdout) == (0, "vantage: nothing queued\n")
    assert _waiting(database) == 0
    assert _run_ids(vantage_server.address) == [run_id]
    on_server = _read_back(vantage_server.address, run_id)
    assert len(on_server["results"]) == _NODE_IDS
    assert on_server == _read_back(serve_local(database).address, run_id)
