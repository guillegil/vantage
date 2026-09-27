"""The outbox: where it lives, who may read it, what it holds, how full it
may get, and sending what it holds -- to a real `vantage` server wherever
the answer matters, so every run sent from it is checked as stored.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sqlite3
import stat
import threading
import time
import urllib.error
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_vantage import outbox as outbox_module
from pytest_vantage import transport
from pytest_vantage.outbox import Outbox, OutboxError, outbox_path, send_queued, worth_retrying
from pytest_vantage.transport import ProjectRefusedError
from vantage.core.domain.execution import Execution
from vantage.service.errors import RejectionError
from vantage_test_server import VantageTestServer

_STARTED = "2026-01-02T03:04:05.000000+00:00"
_FINISHED = "2026-01-02T03:04:06.000000+00:00"


def _result(node_id: str) -> dict[str, object]:
    file_path, _, function_name = node_id.partition("::")
    return {
        "node_id": node_id,
        "file_path": file_path,
        "class_name": None,
        "function_name": function_name,
        "param_id": None,
        "outcome": "passed",
        "setup_outcome": "passed",
        "call_outcome": "passed",
        "teardown_outcome": "passed",
        "duration": 0.01,
        "setup_duration": 0.0,
        "call_duration": 0.01,
        "teardown_duration": 0.0,
        "started_at": _STARTED,
        "finished_at": _FINISHED,
        "worker_id": None,
    }


def _run_reports(
    run_id: str | None = None, *, tests: int = 1, project: str | None = None
) -> list[dict[str, object]]:
    """One session's reports as the plugin sends them: an in-progress report
    with the first result, then the finishing report with the rest. With no
    `project`, they name none, as a run queued before projects did."""
    run_id = run_id or uuid.uuid4().hex
    run = {
        "id": run_id,
        "started_at": _STARTED,
        "finished_at": None,
        "exit_status": None,
        "interrupted": False,
        "interrupt_reason": None,
    }
    finished = {**run, "finished_at": _FINISHED, "exit_status": 0}
    results = [_result(f"test_sample.py::test_{index}") for index in range(tests)]
    vcs = {"commit": None, "branch": None, "commit_subject": None, "dirty": None, "root": None}
    named = {} if project is None else {"project": project}
    return [
        {"run": run, "results": results[:1], "vcs": vcs, **named},
        {"run": finished, "results": results[1:], "vcs": vcs, **named},
    ]


def _run_id(reports: list[dict[str, object]]) -> str:
    run = reports[0]["run"]
    assert isinstance(run, dict)
    run_id: str = run["id"]
    return run_id


def _closed_port_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def box(tmp_path: Path) -> Iterator[Outbox]:
    opened = Outbox(outbox_path(tmp_path / "data" / "vantage.db"))
    try:
        yield opened
    finally:
        opened.close()


def _column(path: Path, column: str) -> list[Any]:
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        return [row[0] for row in conn.execute(f"SELECT {column} FROM entry ORDER BY id")]  # noqa: S608


# --- The file -------------------------------------------------------------------


def test_the_outbox_sits_next_to_the_local_database() -> None:
    assert outbox_path(Path("/data/vantage/vantage.db")) == Path("/data/vantage/vantage.db-outbox")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_new_outbox_is_owner_only_in_an_owner_only_directory(tmp_path: Path) -> None:
    """It holds failure text and metadata. The umask can only take bits
    away, so a permissive one must not widen either."""
    path = tmp_path / "missing" / "vantage.db-outbox"
    previous = os.umask(0o000)
    try:
        Outbox(path).close()
    finally:
        os.umask(previous)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_an_existing_directory_keeps_the_mode_its_owner_chose(tmp_path: Path) -> None:
    directory = tmp_path / "shared"
    directory.mkdir()
    directory.chmod(0o755)

    Outbox(directory / "vantage.db-outbox").close()

    assert stat.S_IMODE(directory.stat().st_mode) == 0o755


def test_a_sqlite_file_that_is_not_an_outbox_is_refused_and_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "vantage.db-outbox"
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE run (id TEXT)")
    conn.close()

    with pytest.raises(OutboxError, match="not a vantage outbox"):
        Outbox(path)

    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master")]
    conn.close()
    assert tables == ["run"]


def test_a_file_that_is_not_sqlite_is_an_outbox_error_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "vantage.db-outbox"
    path.write_bytes(b"not a database, " * 100)

    with pytest.raises(OutboxError, match=re.escape(str(path))):
        Outbox(path)


# --- What it holds ----------------------------------------------------------------


def test_entries_are_counted_per_server_exactly_as_configured(box: Outbox) -> None:
    """`http://host:1` and `http://host:1/` are different addresses as
    configured, and an entry is only ever sent to its own."""
    assert box.enqueue("http://a:1", "1" * 32, _run_reports()) == 1
    assert box.enqueue("http://b:1", "2" * 32, _run_reports()) == 1
    assert box.enqueue("http://a:1", "3" * 32, _run_reports()) == 2
    assert box.enqueue("http://a:1/", "4" * 32, _run_reports()) == 1

    assert box.waiting() == 4
    assert box.waiting("http://a:1") == 2
    assert box.waiting("http://nowhere:1") == 0
    assert box.servers() == ("http://a:1", "http://b:1", "http://a:1/")


def test_an_entry_keeps_its_reports_in_order_across_reopening(tmp_path: Path) -> None:
    path = tmp_path / "vantage.db-outbox"
    reports = _run_reports(tests=3)
    with Outbox(path) as first:
        first.enqueue("http://a:1", _run_id(reports), reports)

    with Outbox(path) as second:
        claimed = second._claim_next("http://a:1", 0, timeout=1.0, budget=1.0)

    assert claimed is not None
    assert claimed.run_id == _run_id(reports)
    assert claimed.reports == reports


# --- Bounds ------------------------------------------------------------------------


def test_the_bounds_are_a_thousand_runs_and_256_mib() -> None:
    assert outbox_module.MAX_ENTRIES == 1000
    assert outbox_module.MAX_REPORT_BYTES == 256 * 1024 * 1024


def test_queueing_past_the_entry_bound_drops_the_oldest_run(
    box: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outbox_module, "MAX_ENTRIES", 3)
    run_ids = [f"{index:032x}" for index in range(5)]

    for run_id in run_ids:
        box.enqueue("http://a:1", run_id, _run_reports(run_id))

    assert box.waiting() == 3
    assert box.evicted == run_ids[:2]
    assert _column(box.path, "run_id") == run_ids[2:]


def test_queueing_past_the_size_bound_drops_the_oldest_runs_until_it_fits(
    box: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    one_run = len(json.dumps(_run_reports("0" * 32)).encode())
    monkeypatch.setattr(outbox_module, "MAX_REPORT_BYTES", 3 * one_run)
    run_ids = [f"{index:032x}" for index in range(3)]
    for run_id in run_ids:
        box.enqueue("http://a:1", run_id, _run_reports(run_id))
    assert box.evicted == []

    bigger = _run_reports("f" * 32, tests=3)
    box.enqueue("http://b:1", "f" * 32, bigger)

    assert box.evicted == run_ids[:2]
    assert _column(box.path, "run_id") == [run_ids[2], "f" * 32]
    assert sum(_column(box.path, "size")) <= 3 * one_run


def test_a_run_larger_than_the_whole_outbox_is_refused_and_drops_nothing(
    box: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.enqueue("http://a:1", "0" * 32, _run_reports("0" * 32))
    monkeypatch.setattr(outbox_module, "MAX_REPORT_BYTES", 100)

    with pytest.raises(OutboxError, match="f" * 32):
        box.enqueue("http://a:1", "f" * 32, _run_reports("f" * 32))

    assert box.evicted == []
    assert box.waiting() == 1


# --- Sending --------------------------------------------------------------------------


def _stored(server: VantageTestServer) -> dict[str, Execution]:
    return {execution.identity.value: execution for execution in server.executions()}


def test_queued_runs_are_sent_oldest_first_and_deleted_once_acknowledged(
    box: Outbox, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = [_run_reports(tests=2) for _ in range(3)]
    for reports in runs:
        box.enqueue(vantage_server.address, _run_id(reports), reports)
    order: list[tuple[str, object]] = []
    real_send = transport.send

    def _send(
        address: str, report: dict[str, object], *, timeout: float, token: str | None = None
    ) -> None:
        run = report["run"]
        assert isinstance(run, dict)
        order.append((run["id"], run["exit_status"]))
        real_send(address, report, timeout=timeout, token=token)

    monkeypatch.setattr(outbox_module, "send", _send)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert (summary.sent, summary.dropped, summary.waiting, summary.stopped) == (3, (), 0, None)
    assert box.waiting() == 0
    stored = _stored(vantage_server)
    assert set(stored) == {_run_id(reports) for reports in runs}
    assert all(execution.exit_status == 0 for execution in stored.values())
    assert len(vantage_server.results()) == 6
    # Oldest first, each run's reports in their own order.
    assert order == [(_run_id(reports), status) for reports in runs for status in (None, 0)]


def test_entries_for_another_server_are_never_sent_or_touched(
    box: Outbox, vantage_server: VantageTestServer
) -> None:
    elsewhere = _run_reports()
    box.enqueue("http://ci-vantage:8765", _run_id(elsewhere), elsewhere)
    here = _run_reports()
    box.enqueue(vantage_server.address, _run_id(here), here)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert summary.sent == 1
    assert set(_stored(vantage_server)) == {_run_id(here)}
    assert box.servers() == ("http://ci-vantage:8765",)
    assert _column(box.path, "attempts") == [0]


class _UnprocessableError(RejectionError):
    status_code = 422
    error = "invalid_report"


class _UnavailableError(RejectionError):
    status_code = 503
    error = "unavailable"


def _refuse_run(
    server: VantageTestServer, monkeypatch: pytest.MonkeyPatch, run_id: str, error: Exception
) -> None:
    real = server.store.record_session

    def _record_session(execution: Execution, **kwargs: Any) -> bool:
        if execution.identity.value == run_id:
            raise error
        return real(execution, **kwargs)

    monkeypatch.setattr(server.store, "record_session", _record_session)


@pytest.mark.parametrize(
    "stored_text",
    ['[{"run": {"id"', '{"run": {}}', '[{"run": {}}, "not a report"]'],
    ids=["not-json", "not-a-list", "not-reports"],
)
def test_a_run_whose_reports_cannot_be_read_back_is_dropped_and_the_rest_sent(
    box: Outbox, vantage_server: VantageTestServer, stored_text: str
) -> None:
    """A damaged entry could never be sent; left in place it would stop
    every later sender at the head of the queue."""
    damaged, accepted = _run_reports(), _run_reports()
    box.enqueue(vantage_server.address, _run_id(damaged), damaged)
    box.enqueue(vantage_server.address, _run_id(accepted), accepted)
    with contextlib.closing(sqlite3.connect(box.path)) as conn, conn:
        conn.execute(
            "UPDATE entry SET reports = ? WHERE run_id = ?", (stored_text, _run_id(damaged))
        )
    conn.close()

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert summary.unreadable == (_run_id(damaged),)
    assert (summary.sent, summary.dropped, summary.waiting, summary.stopped) == (1, (), 0, None)
    assert set(_stored(vantage_server)) == {_run_id(accepted)}
    assert box.waiting() == 0


def test_a_run_the_server_rejects_with_a_4xx_is_dropped_and_the_rest_sent(
    box: Outbox, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected, accepted = _run_reports(), _run_reports()
    _refuse_run(vantage_server, monkeypatch, _run_id(rejected), _UnprocessableError("no"))
    box.enqueue(vantage_server.address, _run_id(rejected), rejected)
    box.enqueue(vantage_server.address, _run_id(accepted), accepted)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert summary.dropped == (_run_id(rejected),)
    assert (summary.sent, summary.waiting, summary.stopped) == (1, 0, None)
    assert set(_stored(vantage_server)) == {_run_id(accepted)}


def test_a_run_answered_with_a_5xx_stays_queued_and_the_rest_are_sent(
    box: Outbox, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5xx may be that one run's problem, so it holds up no other run."""
    failing, accepted = _run_reports(), _run_reports()
    _refuse_run(vantage_server, monkeypatch, _run_id(failing), _UnavailableError("storage is down"))
    box.enqueue(vantage_server.address, _run_id(failing), failing)
    box.enqueue(vantage_server.address, _run_id(accepted), accepted)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert (summary.sent, summary.dropped, summary.waiting, summary.stopped) == (1, (), 1, None)
    assert set(_stored(vantage_server)) == {_run_id(accepted)}
    assert _column(box.path, "run_id") == [_run_id(failing)]
    assert _column(box.path, "attempts") == [1]
    assert "503" in _column(box.path, "last_error")[0]
    assert _column(box.path, "claimed_until") == [None]


# --- A project the server does not have ------------------------------------------


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/api/v1/runs", status, "no", {}, None)  # type: ignore[arg-type]


def test_a_run_refused_for_its_project_is_worth_keeping_and_no_other_404_is() -> None:
    """An admin can add the project, and the same reports are then taken;
    a 404 for anything else, such as a wrong base path, would be the same
    next time."""
    refused = ProjectRefusedError(_http_error(404), "missing", "no project missing")

    assert worth_retrying(refused) is True
    assert outbox_module._rejected(refused) is False
    assert worth_retrying(_http_error(404)) is False
    assert outbox_module._rejected(_http_error(404)) is True


def test_runs_of_a_project_the_server_lacks_stay_queued_and_cost_one_request(
    box: Outbox, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first run of a missing project is refused, kept with its attempt
    counted and the reason; sending moves on, since other runs may name
    projects the server has, and passes over the same project's later runs
    without uploading each to hear the same answer. Once an admin adds the
    projects, the next send delivers every run into its own."""
    first_a = _run_reports(project="missing-a")
    no_project = _run_reports()
    second_a = _run_reports(project="missing-a")
    only_b = _run_reports(project="missing-b")
    named_default = _run_reports(project="default")
    runs = [first_a, no_project, second_a, only_b, named_default]
    for reports in runs:
        box.enqueue(vantage_server.address, _run_id(reports), reports)
    asked: list[str] = []
    real_send = transport.send

    def _send(
        address: str, report: dict[str, object], *, timeout: float, token: str | None = None
    ) -> None:
        run = report["run"]
        assert isinstance(run, dict)
        asked.append(run["id"])
        real_send(address, report, timeout=timeout, token=token)

    monkeypatch.setattr(outbox_module, "send", _send)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert (summary.sent, summary.dropped, summary.waiting, summary.stopped) == (2, (), 3, None)
    assert summary.missing_projects == ("missing-a", "missing-b")
    assert set(_stored(vantage_server)) == {_run_id(no_project), _run_id(named_default)}
    assert _run_id(second_a) not in asked
    assert asked.count(_run_id(first_a)) == 1
    assert asked.count(_run_id(only_b)) == 1
    assert _column(box.path, "run_id") == [_run_id(first_a), _run_id(second_a), _run_id(only_b)]
    assert _column(box.path, "attempts") == [1, 0, 1]
    assert _column(box.path, "claimed_until") == [None, None, None]
    first_error, second_error, b_error = _column(box.path, "last_error")
    assert "vantage project add missing-a" in first_error
    assert second_error is None
    assert "vantage project add missing-b" in b_error

    vantage_server.add_project("missing-a")
    vantage_server.add_project("missing-b")
    retried = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert (retried.sent, retried.waiting, retried.missing_projects) == (3, 0, ())
    assert {run_id: vantage_server.project_of(run_id) for run_id in _stored(vantage_server)} == {
        _run_id(first_a): "missing-a",
        _run_id(no_project): "default",
        _run_id(second_a): "missing-a",
        _run_id(only_b): "missing-b",
        _run_id(named_default): "default",
    }


class _NotFoundError(RejectionError):
    """A 404 that is not about the project."""

    status_code = 404
    error = "not_found"


def test_a_404_that_is_not_about_the_project_still_drops_the_run(
    box: Outbox, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only `unknown_project` is kept: any other 404 is a rejection like
    the rest of the 4xx, and the next run is sent."""
    rejected, accepted = _run_reports(project="default"), _run_reports(project="default")
    _refuse_run(vantage_server, monkeypatch, _run_id(rejected), _NotFoundError("gone"))
    box.enqueue(vantage_server.address, _run_id(rejected), rejected)
    box.enqueue(vantage_server.address, _run_id(accepted), accepted)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert summary.dropped == (_run_id(rejected),)
    assert (summary.sent, summary.waiting, summary.missing_projects) == (1, 0, ())
    assert set(_stored(vantage_server)) == {_run_id(accepted)}


def test_a_run_the_server_holds_in_another_project_is_dropped(
    box: Outbox, vantage_server: VantageTestServer
) -> None:
    """A run never moves: the server answers `409 project_mismatch` to a
    report filing it elsewhere, and asking again would be answered the
    same. The run stays where it was first stored."""
    vantage_server.add_project("elsewhere")
    run_id = uuid.uuid4().hex
    stored = _run_reports(run_id, project="default")
    box.enqueue(vantage_server.address, run_id, stored)
    assert send_queued(box, vantage_server.address, timeout=5.0, budget=30.0).sent == 1
    moved = _run_reports(run_id, project="elsewhere")
    box.enqueue(vantage_server.address, run_id, moved)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert (summary.sent, summary.dropped, summary.waiting) == (0, (run_id,), 0)
    assert summary.missing_projects == ()
    assert vantage_server.project_of(run_id) == "default"


class _RetryLaterError(RejectionError):
    """What a proxy in front of the server answers when it is busy."""

    error = "retry_later"

    def __init__(self, status_code: int) -> None:
        super().__init__("try again later")
        self.status_code = status_code


@pytest.mark.parametrize("status", [408, 429])
def test_a_run_answered_with_408_or_429_stays_queued_and_sending_stops(
    box: Outbox, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Both statuses ask the client to send the same request again later,
    so the run is not dropped; and the next run would be answered the same,
    so it is not tried."""
    first, second = _run_reports(), _run_reports()
    _refuse_run(vantage_server, monkeypatch, _run_id(first), _RetryLaterError(status))
    box.enqueue(vantage_server.address, _run_id(first), first)
    box.enqueue(vantage_server.address, _run_id(second), second)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert (summary.sent, summary.dropped, summary.waiting) == (0, (), 2)
    assert summary.stopped is not None
    assert f"HTTP Error {status}" in summary.stopped
    assert _column(box.path, "run_id") == [_run_id(first), _run_id(second)]
    assert _column(box.path, "attempts") == [1, 0]
    assert _column(box.path, "claimed_until") == [None, None]


def test_an_interrupted_send_gives_its_run_back_at_once(
    box: Outbox, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C still stops sending, but the run it was sending is not left
    claimed until the claim lapses: the next sender takes it at once."""
    reports = _run_reports()
    box.enqueue(vantage_server.address, _run_id(reports), reports)

    def _interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(outbox_module, "send", _interrupted)
        with pytest.raises(KeyboardInterrupt):
            send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert _column(box.path, "claimed_until") == [None]
    assert _column(box.path, "attempts") == [0]
    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)
    assert (summary.sent, summary.waiting) == (1, 0)
    assert set(_stored(vantage_server)) == {_run_id(reports)}


def test_an_unreachable_server_stops_sending_and_keeps_every_run(box: Outbox) -> None:
    address = _closed_port_address()
    for _ in range(3):
        reports = _run_reports()
        box.enqueue(address, _run_id(reports), reports)

    summary = send_queued(box, address, timeout=5.0, budget=30.0)

    assert (summary.sent, summary.dropped, summary.waiting) == (0, (), 3)
    assert summary.stopped is not None
    assert f"{address} is unreachable" in summary.stopped
    # Stopped at the first: the others were never tried.
    assert _column(box.path, "attempts") == [1, 0, 0]


def test_an_answer_that_is_not_a_vantage_servers_stops_sending_and_keeps_the_run(
    box: Outbox,
) -> None:
    """Something else listening at the address answers 2xx without
    acknowledging the run: a later session may reach the real server."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"

    def _answer_ok() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")

    answering = threading.Thread(target=_answer_ok, daemon=True)
    answering.start()
    reports = _run_reports()[1:]
    box.enqueue(address, _run_id(reports), reports)
    try:
        summary = send_queued(box, address, timeout=5.0, budget=30.0)
    finally:
        answering.join(timeout=5)
        listener.close()

    assert (summary.sent, summary.dropped, summary.waiting) == (0, (), 1)
    assert summary.stopped is not None
    assert "without acknowledging" in summary.stopped


def test_sending_stops_when_the_budget_is_spent_and_counts_no_attempt(
    box: Outbox, vantage_server: VantageTestServer
) -> None:
    reports = _run_reports()
    box.enqueue(vantage_server.address, _run_id(reports), reports)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=0.0)

    assert (summary.sent, summary.waiting) == (0, 1)
    assert summary.stopped is not None
    assert "ran out" in summary.stopped
    assert _column(box.path, "attempts") == [0]
    assert _column(box.path, "claimed_until") == [None]
    assert vantage_server.requests == []


@pytest.mark.slow
def test_a_server_that_never_answers_holds_sending_no_longer_than_the_budget(
    box: Outbox,
) -> None:
    """Each report waits for at most what is left of the budget, not its
    full timeout."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()  # never accepted: connects, and never answers
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    reports = _run_reports()
    box.enqueue(address, _run_id(reports), reports)
    try:
        started = time.monotonic()
        summary = send_queued(box, address, timeout=30.0, budget=0.5)
        elapsed = time.monotonic() - started
    finally:
        listener.close()

    assert elapsed < 3.0
    assert summary.stopped is not None
    assert "ran out" in summary.stopped
    assert summary.waiting == 1


# --- Several flushers at once ------------------------------------------------------------


def test_concurrent_flushers_send_every_run_exactly_once(
    tmp_path: Path, vantage_server: VantageTestServer
) -> None:
    """Each flusher has its own connection to the file, as separate pytest
    sessions do; a claim is taken in a write transaction, so no two send
    the same run."""
    path = tmp_path / "vantage.db-outbox"
    runs = [_run_reports() for _ in range(24)]
    with Outbox(path) as filling:
        for reports in runs:
            filling.enqueue(vantage_server.address, _run_id(reports), reports)
    sent: list[int] = []
    failures: list[BaseException] = []
    start = threading.Barrier(4)

    def _flush() -> None:
        try:
            with Outbox(path) as mine:
                start.wait(timeout=10)
                summary = send_queued(mine, vantage_server.address, timeout=5.0, budget=60.0)
            sent.append(summary.sent)
        except BaseException as exc:  # reported below, not lost on the thread
            failures.append(exc)

    flushers = [threading.Thread(target=_flush) for _ in range(4)]
    for flusher in flushers:
        flusher.start()
    for flusher in flushers:
        flusher.join(timeout=60)

    assert failures == []
    assert sum(sent) == len(runs)
    assert len(vantage_server.requests) == 2 * len(runs)
    assert set(_stored(vantage_server)) == {_run_id(reports) for reports in runs}
    with Outbox(path) as after:
        assert after.waiting() == 0


def test_a_claim_is_respected_until_it_lapses(
    box: Outbox, vantage_server: VantageTestServer
) -> None:
    """A flusher killed mid-send leaves its claim behind; the entry is sent
    again once the claim has lapsed, and not before."""
    held, abandoned = _run_reports(), _run_reports()
    box.enqueue(vantage_server.address, _run_id(held), held)
    box.enqueue(vantage_server.address, _run_id(abandoned), abandoned)
    with contextlib.closing(sqlite3.connect(box.path)) as conn, conn:
        conn.execute(
            "UPDATE entry SET claimed_until = ? WHERE run_id = ?",
            (time.time() + 600, _run_id(held)),
        )
        conn.execute(
            "UPDATE entry SET claimed_until = ? WHERE run_id = ?",
            (time.time() - 1, _run_id(abandoned)),
        )
    conn.close()

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert (summary.sent, summary.waiting) == (1, 1)
    assert set(_stored(vantage_server)) == {_run_id(abandoned)}
    assert _column(box.path, "run_id") == [_run_id(held)]


def test_a_run_sent_twice_is_stored_once(box: Outbox, vantage_server: VantageTestServer) -> None:
    """What makes a lapsed claim, or a timeout the server survived, safe to
    send again."""
    reports = _run_reports(tests=3)
    for _ in range(2):
        box.enqueue(vantage_server.address, _run_id(reports), reports)

    summary = send_queued(box, vantage_server.address, timeout=5.0, budget=30.0)

    assert summary.sent == 2
    assert list(_stored(vantage_server)) == [_run_id(reports)]
    assert len(vantage_server.results()) == 3
