"""Concurrent writers on one database file never corrupt or drop each
other's writes, a thread never reads another's write half-done, and a slow
store call never holds up an unrelated request.

Every thread is joined with a timeout: a deadlock is exactly what these tests
look for, and an unbounded `join` would hang the suite instead of failing it.
For the same reason every thread is a daemon: a thread still stuck when its
test has failed must not keep the interpreter from exiting afterwards.
"""

from __future__ import annotations

import contextlib
import importlib.resources
import sqlite3
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from vantage.core.domain.access import (
    MANAGE_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    SCOPES,
    new_token,
    token_digest,
)
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.passwords import hash_password
from vantage.core.domain.projects import DEFAULT_PROJECT, OWNER_ROLE, Project
from vantage.core.domain.result import Result
from vantage.core.domain.sections import MAX_SECTIONS
from vantage.core.ports.storage import (
    EMPTY_RUN_METADATA,
    ExecutionStore,
    ProjectExistsError,
    RunMetadata,
)
from vantage.service.access import SESSION_COOKIE
from vantage.service.app import create_app
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE
from vantage.storage import connection, sqlite_store
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _result, _start_only_execution, check_comparison

# `any_store`, for each adapter in turn.
pytest_plugins = ["store_fixtures", "password_fixtures"]

_JOIN_TIMEOUT_SECONDS = 10


def _execution(hex_id: str) -> Execution:
    started = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    return Execution(
        identity=Identity(hex_id),
        started_at=started,
        finished_at=started + timedelta(seconds=5),
        exit_status=0,
        interrupted=False,
        interrupt_reason=None,
    )


def _run_concurrently(targets: list[Callable[[], object]]) -> list[BaseException]:
    """Start every target at once, behind a barrier, and return whatever they
    raised. Fails if any thread is still running after the timeout."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(targets))

    def _wrap(target: Callable[[], object]) -> Callable[[], None]:
        def _run() -> None:
            try:
                barrier.wait()
                target()
            except BaseException as exc:  # noqa: BLE001 -- collected for the caller's assertion
                errors.append(exc)

        return _run

    threads = [threading.Thread(target=_wrap(target), daemon=True) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not any(thread.is_alive() for thread in threads), "a thread never finished"
    return errors


def test_two_concurrent_sessions_both_leave_a_run_entry(tmp_path: Path) -> None:
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        ids = ["a" * 32, "b" * 32]
        created: dict[str, bool] = {}

        def _report(hex_id: str) -> None:
            created[hex_id] = store.record_session(
                _execution(hex_id),
                results=(),
                received_at=datetime.now(timezone.utc),
                project=DEFAULT_PROJECT,
            )

        errors = _run_concurrently([partial(_report, hex_id) for hex_id in ids])

        assert errors == []
        assert store.count_executions() == 2
        assert created == {"a" * 32: True, "b" * 32: True}
        stored_ids = {store.get_execution(hex_id).identity.value for hex_id in ids}  # type: ignore[union-attr]
        assert stored_ids == set(ids)
    finally:
        store.close()


def test_two_concurrent_two_hundred_test_sessions_leave_four_hundred_results(
    tmp_path: Path,
) -> None:
    """Two sessions, each reporting 200 results, racing through the store's
    `threading.Lock` and `BEGIN IMMEDIATE` transaction, leave exactly 400
    result rows -- neither batch clobbers or drops rows from the other.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        sessions = {"a" * 32: "x", "b" * 32: "y"}

        def _report(hex_id: str, prefix: str) -> None:
            results = [_result(f"t.py::test_{prefix}_{i}") for i in range(200)]
            store.record_session(
                _execution(hex_id),
                results=results,
                received_at=datetime.now(timezone.utc),
                project=DEFAULT_PROJECT,
            )

        errors = _run_concurrently(
            [partial(_report, hex_id, prefix) for hex_id, prefix in sessions.items()]
        )

        assert errors == []
        assert store.count_results() == 400
    finally:
        store.close()


def test_ten_simultaneous_sessions_leave_ten_run_entries_and_raise_nothing(
    tmp_path: Path,
) -> None:
    """Ten sessions reporting at once each leave their own run entry and
    none raises -- the store's lock serialises the ten transactions rather
    than letting any of them fail.
    """
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    try:
        # Single hex digits (0-9) are already valid lowercase hex, so
        # `str(i) * 32` yields ten distinct, valid 32-char identities.
        session_ids = [str(i) * 32 for i in range(10)]

        def _report(hex_id: str) -> None:
            store.record_session(
                _execution(hex_id),
                results=(),
                received_at=datetime.now(timezone.utc),
                project=DEFAULT_PROJECT,
            )

        errors = _run_concurrently([partial(_report, hex_id) for hex_id in session_ids])

        assert errors == []
        assert store.count_executions() == 10
    finally:
        store.close()


def _park_the_writer_inside_its_transaction(
    monkeypatch: pytest.MonkeyPatch, *, then_fail: bool
) -> tuple[threading.Event, threading.Event]:
    """Stop `record_session` after its run row is written and before its
    results are: `inside` is set once it is parked, and it carries on (or
    raises, rolling everything back) once `release` is set."""
    inside = threading.Event()
    release = threading.Event()
    resolve = sqlite_store._resolve_test_case_ids

    def _parked(conn: sqlite3.Connection, project: str, node_ids: Sequence[str]) -> dict[str, int]:
        inside.set()
        release.wait(_JOIN_TIMEOUT_SECONDS)
        if then_fail:
            raise RuntimeError("the write fails after its run row")
        return resolve(conn, project, node_ids)

    monkeypatch.setattr(sqlite_store, "_resolve_test_case_ids", _parked)
    return inside, release


@pytest.mark.parametrize("then_fail", [False, True], ids=["commits", "rolls-back"])
def test_a_read_never_sees_another_threads_write_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, then_fail: bool
) -> None:
    """Every thread shares one connection, so a read that ran while another
    thread was inside `BEGIN IMMEDIATE` would run inside that transaction:
    it would see the run before its results, or a run that is then rolled
    back. A read must wait for the write to finish instead."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    inside, release = _park_the_writer_inside_its_transaction(monkeypatch, then_fail=then_fail)
    run_id = "a" * 32
    results = [_result(f"t.py::test_{i}") for i in range(5)]
    observed: list[tuple[bool, int, int, int]] = []

    def _write() -> None:
        with contextlib.suppress(RuntimeError):
            store.record_session(
                _execution(run_id),
                results=results,
                received_at=datetime.now(timezone.utc),
                project=DEFAULT_PROJECT,
            )

    def _read() -> None:
        observed.append(
            (
                store.get_execution(run_id) is not None,
                len(store.get_results(run_id)),
                store.count_executions(),
                len(store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items),
            )
        )

    try:
        writer = threading.Thread(target=_write, daemon=True)
        writer.start()
        assert inside.wait(_JOIN_TIMEOUT_SECONDS)
        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        # A reader that does not wait for the writer has finished by now.
        reader.join(timeout=0.2)
        release.set()
        writer.join(timeout=_JOIN_TIMEOUT_SECONDS)
        reader.join(timeout=_JOIN_TIMEOUT_SECONDS)
        assert not writer.is_alive()
        assert not reader.is_alive()

        assert observed == [(False, 0, 0, 0) if then_fail else (True, 5, 1, 1)]
    finally:
        release.set()
        store.close()


def test_every_read_returns_rather_than_waiting_on_the_stores_own_lock(tmp_path: Path) -> None:
    """Each read takes the store's lock; one that called another locking
    read while holding it would wait on itself forever."""
    store = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    run_id = "a" * 32
    store.record_session(
        _execution(run_id),
        results=[_result("t.py::test_a")],
        received_at=datetime.now(timezone.utc),
        project=DEFAULT_PROJECT,
    )
    reads: list[Callable[[], object]] = [
        partial(store.get_execution, run_id),
        store.count_executions,
        partial(store.get_results, run_id),
        store.count_results,
        partial(store.get_catalogue_entry, "t.py::test_a", project=DEFAULT_PROJECT),
        partial(store.list_runs, limit=10, offset=0, project=DEFAULT_PROJECT),
        partial(
            store.list_runs_with_metadata_horizon,
            filters=[("k", "v")],
            limit=10,
            offset=0,
            project=DEFAULT_PROJECT,
        ),
        partial(store.get_run_detail, run_id),
        partial(store.get_run_metadata, run_id),
        partial(store.list_results, run_id, limit=10, offset=0),
        partial(store.get_result, run_id, node_id="t.py::test_a"),
        partial(
            store.list_history,
            node_id="t.py::test_a",
            limit=10,
            offset=0,
            project=DEFAULT_PROJECT,
        ),
        partial(store.list_settings, "test_sections", project=DEFAULT_PROJECT),
        partial(store.get_run_case_outcomes, run_id),
        partial(store.count_outcomes, [run_id]),
        partial(store.list_results, run_id, limit=10, offset=0, outcomes=["passed"]),
        partial(store.get_project, DEFAULT_PROJECT),
        store.list_projects,
    ]
    try:
        assert _run_concurrently(reads) == []
    finally:
        store.close()


def test_two_stores_on_one_file_both_land_every_session(tmp_path: Path) -> None:
    """Two server processes on one database each hold their own connection,
    which no in-process lock can serialise. WAL, `BEGIN IMMEDIATE` and the
    busy timeout must make them take turns rather than fail with "database
    is locked"."""
    db_path = tmp_path / "store" / "vantage.db"
    stores = [SqliteExecutionStore(db_path), SqliteExecutionStore(db_path)]
    try:

        def _report(store: SqliteExecutionStore, prefix: str) -> None:
            for i in range(20):
                store.record_session(
                    _execution(f"{prefix}{i:031x}"),
                    results=[_result(f"t.py::test_{prefix}_{i}_{j}") for j in range(20)],
                    received_at=datetime.now(timezone.utc),
                    project=DEFAULT_PROJECT,
                )

        errors = _run_concurrently(
            [partial(_report, store, prefix) for store, prefix in zip(stores, "ab")]
        )

        assert errors == []
        assert stores[0].count_executions() == 40
        assert stores[1].count_results() == 800
    finally:
        for store in stores:
            store.close()


def _branch_run(index: int, *, stopped_early: bool = False) -> Execution:
    """Run `index` of `main`, a minute after the one before; a run stopped
    early is no one's baseline."""
    started = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=index)
    return Execution(
        identity=Identity(f"{index + 1:032x}"),
        started_at=started,
        finished_at=started + timedelta(seconds=30),
        exit_status=1,
        interrupted=False,
        interrupt_reason="stopping after 1 failures" if stopped_early else None,
        vcs=VcsContext(
            commit="a" * 40,
            branch="main",
            commit_subject=None,
            commit_subject_truncated=False,
            dirty=False,
            root=None,
        ),
    )


def _branch_results(index: int) -> list[Result]:
    """Twenty tests, a few failing, which ones moving from run to run, and
    one test of the run's own."""
    return [
        _result(f"t.py::test_{test}", outcome="failed" if (index + test) % 7 == 0 else "passed")
        for test in range(20)
    ] + [_result(f"t.py::test_only_in_{index}")]


def test_two_stores_finishing_runs_of_one_branch_at_once_compare_each_run_once(
    tmp_path: Path,
) -> None:
    """Two processes on one file finish runs of one branch in turns: each
    finish is compared, inside its own write transaction, with a complete
    run committed before it, and stores exactly the changes against that
    run."""
    db_path = tmp_path / "store" / "vantage.db"
    stores = [SqliteExecutionStore(db_path), SqliteExecutionStore(db_path)]
    try:

        def _finish(store: SqliteExecutionStore, parity: int) -> None:
            for index in range(parity, 30, 2):
                store.record_session(
                    _branch_run(index, stopped_early=index % 5 == 4),
                    results=_branch_results(index),
                    received_at=datetime.now(timezone.utc),
                    project=DEFAULT_PROJECT,
                )

        errors = _run_concurrently(
            [partial(_finish, store, parity) for parity, store in enumerate(stores)]
        )

        assert errors == []
        baselines = [check_comparison(stores[0], f"{index + 1:032x}") for index in range(30)]
        assert baselines[0] is None
        # A thread's own run two minutes earlier committed first, so from
        # the third run on every run has a baseline, and none stopped early.
        assert all(baseline is not None for baseline in baselines[2:])
        assert not {f"{index + 1:032x}" for index in range(4, 30, 5)} & set(baselines)
    finally:
        for store in stores:
            store.close()


def test_stores_opening_one_empty_database_at_once_leave_one_default_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every opener finds the new file empty and writes the schema, the
    stamp and `default`; the ones after the first must find `default`
    written and leave it, not fail on it or add a second."""
    openers = 4
    # Each opener waits here until all have looked, so every one of them
    # finds the file empty and writes the schema, however fast the first is.
    all_looked = threading.Barrier(openers, timeout=_JOIN_TIMEOUT_SECONDS)
    look = connection._schema_objects

    def _look_together(conn: sqlite3.Connection) -> set[str]:
        found = look(conn)
        all_looked.wait()
        return found

    monkeypatch.setattr(connection, "_schema_objects", _look_together)
    db_path = tmp_path / "store" / "vantage.db"
    opened: list[SqliteExecutionStore] = []

    def _open() -> None:
        opened.append(SqliteExecutionStore(db_path))

    try:
        errors = _run_concurrently([_open for _ in range(openers)])

        assert errors == []
        assert len(opened) == openers
        for store in opened:
            assert [project.name for project in store.list_projects()] == [DEFAULT_PROJECT]
    finally:
        for store in opened:
            store.close()


def _with_frequent_thread_switches(run: Callable[[], list[BaseException]]) -> list[BaseException]:
    """`run()`, switching threads far more often than usual. Pure-Python work
    rarely yields the GIL mid-call at the default interval; switching often is
    what makes a race in the in-memory adapter likely enough to be caught."""
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        return run()
    finally:
        sys.setswitchinterval(interval)


def test_racing_to_create_one_project_creates_it_once(any_store: ExecutionStore) -> None:
    """Checking that the name is free and then writing it would let every
    racer see it free. Exactly one racer creates the project, every other
    one is told it exists, and the project is listed once."""
    racers = 16
    created: list[Project] = []
    refused: list[int] = []
    now = datetime.now(timezone.utc)

    def _create(index: int) -> None:
        try:
            created.append(any_store.create_project("firmware", created_at=now))
        except ProjectExistsError:
            refused.append(index)

    errors = _with_frequent_thread_switches(
        lambda: _run_concurrently([partial(_create, index) for index in range(racers)])
    )

    assert errors == []
    assert len(created) == 1
    assert len(refused) == racers - 1
    assert [project.name for project in any_store.list_projects()] == [DEFAULT_PROJECT, "firmware"]


def test_concurrent_reports_of_one_node_id_into_two_projects_keep_a_catalogue_each(
    any_store: ExecutionStore,
) -> None:
    """Two projects run the same tests at once. Each keeps its own catalogue
    row per node id -- first and last seen within its own runs -- and each
    project's history holds its own runs alone, every result once: a report
    that found or wrote the other project's row would mix the two."""
    now = datetime.now(timezone.utc)
    any_store.create_project("firmware", created_at=now)
    node_ids = [f"tests/test_shared.py::test_{index:02d}" for index in range(20)]
    base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    runs = {
        DEFAULT_PROJECT: [f"a{index:031x}" for index in range(4)],
        "firmware": [f"b{index:031x}" for index in range(4)],
    }
    started = {
        run_id: base + timedelta(minutes=index)
        for project_runs in runs.values()
        for index, run_id in enumerate(project_runs)
    }

    def _report(project: str, run_id: str) -> None:
        any_store.record_session(
            _start_only_execution(run_id, started=started[run_id]),
            results=[_result(node_id) for node_id in node_ids],
            received_at=now,
            project=project,
        )

    errors = _with_frequent_thread_switches(
        lambda: _run_concurrently(
            [
                partial(_report, project, run_id)
                for project, project_runs in runs.items()
                for run_id in project_runs
            ]
        )
    )

    assert errors == []
    assert any_store.count_results() == len(started) * len(node_ids)
    for project, project_runs in runs.items():
        for node_id in node_ids:
            entry = any_store.get_catalogue_entry(node_id, project=project)
            assert entry is not None
            assert entry.first_seen_at == started[project_runs[0]]
            assert entry.last_seen_at == started[project_runs[-1]]
            assert entry.last_seen_run_id == project_runs[-1]
            history = any_store.list_history(project=project, node_id=node_id, limit=50, offset=0)
            assert [item.run_id for item in history.items] == project_runs[::-1]


# --- The HTTP layer ------------------------------------------------------------
#
# `TestClient` used as a context manager runs every request on one event
# loop, as uvicorn does, so a request sent from a second thread shares that
# loop with one still in flight.


def _report(run_id: str) -> dict[str, Any]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-08-15T09:14:02.481930+00:00",
            "finished_at": "2026-08-15T09:14:47.002118+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }


class _ParkedWriteStore(InMemoryExecutionStore):
    """Holds every `record_session` until `release` is set, before it takes
    the store's lock: the report stays in flight for as long as the test
    needs, and only the way a route calls the store can hold up another
    request."""

    def __init__(self) -> None:
        super().__init__()
        self.writing = threading.Event()
        self.release = threading.Event()

    def record_session(
        self,
        execution: Execution,
        *,
        project: str,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
        recorded_by: str | None = None,
    ) -> bool:
        self.writing.set()
        self.release.wait(2 * _JOIN_TIMEOUT_SECONDS)
        return super().record_session(
            execution,
            project=project,
            results=results,
            received_at=received_at,
            metadata=metadata,
            recorded_by=recorded_by,
        )


def test_a_slow_store_write_holds_up_no_other_request() -> None:
    """A store call made on the event loop stalls every request until it
    returns -- heartbeats included, and a session that cannot heartbeat
    reads as abandoned. With one report's write parked inside the store, a
    heartbeat for another run and the capability check must both answer."""
    store = _ParkedWriteStore()
    live_run = "b" * 32
    # The base class's method, which does not park: a run already in progress.
    InMemoryExecutionStore.record_session(
        store,
        _start_only_execution(live_run),
        results=(),
        received_at=datetime.now(timezone.utc),
        project=DEFAULT_PROJECT,
    )
    answered: dict[str, int] = {}

    with TestClient(create_app(store)) as client:

        def _write() -> None:
            answered["write"] = client.post("/api/v1/runs", json=_report("a" * 32)).status_code

        def _others() -> None:
            answered["capabilities"] = client.get("/api/v1/capabilities").status_code
            answered["heartbeat"] = client.post(f"/api/v1/runs/{live_run}/heartbeat").status_code

        writer = threading.Thread(target=_write, daemon=True)
        others = threading.Thread(target=_others, daemon=True)
        writer.start()
        try:
            assert store.writing.wait(_JOIN_TIMEOUT_SECONDS)
            others.start()
            others.join(timeout=_JOIN_TIMEOUT_SECONDS)
            assert not others.is_alive(), "a request waited for another request's store write"
            assert "write" not in answered
        finally:
            store.release.set()
            writer.join(timeout=_JOIN_TIMEOUT_SECONDS)
            others.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert not writer.is_alive()
    assert answered == {"capabilities": 200, "heartbeat": 200, "write": 201}


_HELD_RUN = "c" * 32
_HELD_NODE = "tests/test_held.py::test_x"
_HELD_PROJECT = "firmware"
# A run of `bob`'s in `_HELD_PROJECT`.
_HELD_MEMBER_RUN = "e" * 32
# Two finished runs of `default`, the later compared with the earlier, so
# the routes read its change counts too.
_HELD_BASELINE = "1" * 32
_HELD_COMPARED = "2" * 32

# Each route that reaches the store, with the store method it calls after
# authenticating, keyed by the operation id the document gives it (the
# metadata filter is one more way to call `list_runs`). Every request is an
# admin's, holding every scope, on a store with a user `bob`, an owner of
# `_HELD_PROJECT`, and a token of his, id 2.
_BOB_PASSWORD = "bob's password, long enough"  # noqa: S105
_BOB_TOKEN = new_token()
_SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}

_STORE_ROUTES: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "create_run": ("POST", "/api/v1/runs", {"json": _report("d" * 32)}, "record_session"),
    "heartbeat": ("POST", f"/api/v1/runs/{_HELD_RUN}/heartbeat", {}, "get_run_detail"),
    "list_projects": ("GET", "/api/v1/projects", {}, "list_projects"),
    "create_project": ("POST", "/api/v1/projects", {"json": {"name": "held"}}, "create_project"),
    "list_runs": ("GET", "/api/v1/projects/default/runs", {}, "list_runs"),
    "list_runs_by_metadata": (
        "GET",
        "/api/v1/projects/default/runs",
        {"params": {"metadata_key": "k", "metadata_value": "v"}},
        "list_runs_with_metadata_horizon",
    ),
    "get_run_detail": ("GET", f"/api/v1/runs/{_HELD_RUN}", {}, "get_run_detail"),
    "get_run_metadata": ("GET", f"/api/v1/runs/{_HELD_RUN}/metadata", {}, "get_run_metadata"),
    "list_results": ("GET", f"/api/v1/runs/{_HELD_RUN}/results", {}, "get_run_detail"),
    "get_run_outcomes": ("GET", f"/api/v1/runs/{_HELD_RUN}/outcomes", {}, "get_run_detail"),
    "get_result": (
        "GET",
        f"/api/v1/runs/{_HELD_RUN}/result",
        {"params": {"node_id": _HELD_NODE}},
        "get_run_detail",
    ),
    "list_history": (
        "GET",
        "/api/v1/projects/default/tests/history",
        {"params": {"node_id": _HELD_NODE}},
        "list_history",
    ),
    "list_sections": ("GET", "/api/v1/projects/default/config/sections", {}, "list_settings"),
    "upsert_section": (
        "POST",
        "/api/v1/projects/default/config/sections",
        {"json": {"name": "Held", "prefix": "tests/held"}},
        "upsert_setting",
    ),
    "delete_section": (
        "DELETE",
        "/api/v1/projects/default/config/sections",
        {"params": {"name": "Seeded"}},
        "delete_setting",
    ),
    "get_run_sections": ("GET", f"/api/v1/runs/{_HELD_RUN}/sections", {}, "get_run_detail"),
    "list_members": ("GET", f"/api/v1/projects/{_HELD_PROJECT}/members", {}, "list_members"),
    "set_member": (
        "PUT",
        f"/api/v1/projects/{_HELD_PROJECT}/members/bob",
        {"json": {"role": "editor"}},
        "set_member",
    ),
    "remove_member": (
        "DELETE",
        f"/api/v1/projects/{_HELD_PROJECT}/members/bob",
        {},
        "remove_member",
    ),
    "list_users": ("GET", "/api/v1/users", {}, "list_users"),
    "create_user": ("POST", "/api/v1/users", {"json": {"name": "carol"}}, "create_user"),
    "update_user": ("PATCH", "/api/v1/users/bob", {"json": {"admin": True}}, "update_user"),
    "list_tokens": ("GET", "/api/v1/tokens", {}, "list_tokens"),
    "create_token": ("POST", "/api/v1/tokens", {"json": {"user": "bob"}}, "get_user"),
    "revoke_token": ("POST", "/api/v1/tokens/2/revoke", {}, "revoke_token"),
    "set_password": (
        "PUT",
        "/api/v1/users/bob/password",
        {"json": {"password": "a new password for bob"}},
        "set_password",
    ),
    "log_in": (
        "POST",
        "/api/v1/login",
        {"json": {"name": "bob", "password": _BOB_PASSWORD}},
        "get_password_hash",
    ),
    "sign_in": (
        "POST",
        "/api/v1/session",
        {"json": {"name": "bob", "password": _BOB_PASSWORD}, "headers": _SAME_ORIGIN},
        "get_password_hash",
    ),
    "get_session": ("GET", "/api/v1/session", {}, "authenticate"),
    "sign_out": (
        "DELETE",
        "/api/v1/session",
        {"headers": {"Cookie": f"{SESSION_COOKIE}={_BOB_TOKEN}", **_SAME_ORIGIN}},
        "authenticate",
    ),
    "change_password": (
        "POST",
        "/api/v1/password",
        {
            "json": {
                "name": "bob",
                "password": _BOB_PASSWORD,
                "new_password": "a new password for bob",
            }
        },
        "get_password_hash",
    ),
}


# A route under `/projects/{project}` looks its project up before its own
# store call, in a dependency of its own, so that lookup is held too.
_PROJECT_LOOKUPS: dict[str, tuple[str, str, dict[str, Any], str]] = {
    f"{operation}-project": (method, path, request_kwargs, "get_project")
    for operation, (method, path, request_kwargs, _held) in _STORE_ROUTES.items()
    if path.startswith("/api/v1/projects/")
}

# A member's role is read from the store as well, once the project or the
# run is found, or, for a report, once it is read and valid; an admin's is
# not, so these are `bob`'s requests, one per way a route comes to it.
_BOBS = {"headers": {"Authorization": f"Bearer {_BOB_TOKEN}"}}
_ROLE_LOOKUPS: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "list_runs-role": ("GET", f"/api/v1/projects/{_HELD_PROJECT}/runs", _BOBS, "get_member_role"),
    "get_run_detail-role": (
        "GET",
        f"/api/v1/runs/{_HELD_MEMBER_RUN}",
        _BOBS,
        "get_member_role",
    ),
    "heartbeat-role": (
        "POST",
        f"/api/v1/runs/{_HELD_MEMBER_RUN}/heartbeat",
        _BOBS,
        "get_member_role",
    ),
    "create_run-role": (
        "POST",
        "/api/v1/runs",
        {"json": {**_report("f" * 32), "project": _HELD_PROJECT}, **_BOBS},
        "get_member_role",
    ),
}

# A run's counts and its outcomes are read by the route itself, after the
# page or the run is found, so that later call is held too.
_ROUTE_READS: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "list_runs-counts": ("GET", "/api/v1/projects/default/runs", {}, "count_outcomes"),
    "get_run_detail-counts": ("GET", f"/api/v1/runs/{_HELD_RUN}", {}, "count_outcomes"),
    "list_runs-changes": ("GET", "/api/v1/projects/default/runs", {}, "count_changes"),
    "get_run_detail-changes": ("GET", f"/api/v1/runs/{_HELD_COMPARED}", {}, "count_changes"),
    "get_run_outcomes-outcomes": (
        "GET",
        f"/api/v1/runs/{_HELD_RUN}/outcomes",
        {},
        "get_run_case_outcomes",
    ),
}


def test_every_operation_that_reads_the_store_is_held_below() -> None:
    """The table is checked against the document, so a route added later
    cannot be left out of the event-loop check: every documented operation
    but the two that never touch the store."""
    document = yaml.safe_load(
        importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
    )
    operations = {
        operation["operationId"]
        for methods in document["paths"].values()
        for operation in methods.values()
    }

    assert operations - {"capabilities", "get_openapi_document"} == set(_STORE_ROUTES) - {
        "list_runs_by_metadata"
    }


def _hold(store: InMemoryExecutionStore, method: str) -> tuple[threading.Event, threading.Event]:
    """Make `store.<method>` wait until `release` is set, after setting
    `entered`. An instance attribute shadows the class's method, so every
    route that calls it through the store gets the held one."""
    entered = threading.Event()
    release = threading.Event()
    original = getattr(store, method)

    def _held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        release.wait(2 * _JOIN_TIMEOUT_SECONDS)
        return original(*args, **kwargs)

    setattr(store, method, _held)
    return entered, release


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "held"),
    [
        *_STORE_ROUTES.values(),
        *_PROJECT_LOOKUPS.values(),
        *_ROLE_LOOKUPS.values(),
        *_ROUTE_READS.values(),
    ],
    ids=[*_STORE_ROUTES, *_PROJECT_LOOKUPS, *_ROLE_LOOKUPS, *_ROUTE_READS],
)
def test_a_held_store_call_holds_up_no_other_request(
    method: str, path: str, request_kwargs: dict[str, Any], held: str, cheap_passwords: None
) -> None:
    """A route that called the store on the event loop would stall every
    other request until the call returned -- heartbeats included, and a
    session that cannot heartbeat reads as abandoned. With each route's
    store call held, the capability check must still answer."""
    store = InMemoryExecutionStore()
    now = datetime.now(timezone.utc)
    admin = new_token()
    store.create_user("alice", admin=True, created_at=now)
    store.create_token(
        "alice", digest=token_digest(admin), label="", scopes=frozenset(SCOPES), created_at=now
    )
    store.create_user("bob", admin=False, created_at=now)
    store.create_token(
        "bob",
        digest=token_digest(_BOB_TOKEN),
        label="",
        scopes=frozenset({READ_SCOPE, RECORD_SCOPE, MANAGE_SCOPE}),
        created_at=now,
    )
    store.set_password("bob", password_hash=hash_password(_BOB_PASSWORD), changed_at=now)
    store.create_project(_HELD_PROJECT, created_at=now)
    store.set_member("bob", project=_HELD_PROJECT, role=OWNER_ROLE)
    store.record_session(
        _start_only_execution(_HELD_RUN),
        results=[_result(_HELD_NODE)],
        received_at=now,
        recorded_by="alice",
        project=DEFAULT_PROJECT,
    )
    store.record_session(
        _start_only_execution(_HELD_MEMBER_RUN),
        results=[],
        received_at=now,
        recorded_by="bob",
        project=_HELD_PROJECT,
    )
    for finished in (_HELD_BASELINE, _HELD_COMPARED):
        store.record_session(
            _execution(finished),
            results=[_result(_HELD_NODE)],
            received_at=now,
            recorded_by="alice",
            project=DEFAULT_PROJECT,
        )
    store.upsert_setting(
        TEST_SECTIONS_NAMESPACE,
        "Seeded",
        value='{"prefix": "tests/seeded/"}',
        updated_at=now,
        project=DEFAULT_PROJECT,
    )
    entered, release = _hold(store, held)
    answered: dict[str, int] = {}

    with TestClient(create_app(store), headers={"Authorization": f"Bearer {admin}"}) as client:

        def _held_request() -> None:
            answered["held"] = client.request(method, path, **request_kwargs).status_code

        def _capabilities() -> None:
            answered["capabilities"] = client.get("/api/v1/capabilities").status_code

        holder = threading.Thread(target=_held_request, daemon=True)
        other = threading.Thread(target=_capabilities, daemon=True)
        holder.start()
        try:
            assert entered.wait(_JOIN_TIMEOUT_SECONDS), f"{path} never called {held}"
            other.start()
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)
            assert not other.is_alive(), f"a request waited for {path}'s {held}"
            assert "held" not in answered
        finally:
            release.set()
            holder.join(timeout=_JOIN_TIMEOUT_SECONDS)
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert not holder.is_alive()
    assert answered["capabilities"] == 200
    assert 200 <= answered["held"] < 300


@pytest.mark.parametrize("held", ["access_required", "authenticate"])
def test_authenticating_holds_up_no_other_request(held: str) -> None:
    """Authenticating reads the store, before any route does: asking
    whether a user exists on a server that has none, or looking a token up
    on one that has. Done on the event loop, it would stall every request
    as a slow route would."""
    store = InMemoryExecutionStore()
    headers = {}
    if held == "authenticate":
        store.create_user("alice", admin=False, created_at=datetime.now(timezone.utc))
        token = new_token()
        store.create_token(
            "alice",
            digest=token_digest(token),
            label="",
            scopes=frozenset({READ_SCOPE}),
            created_at=datetime.now(timezone.utc),
        )
        headers = {"Authorization": f"Bearer {token}"}
    entered, release = _hold(store, held)
    answered: dict[str, int] = {}

    with TestClient(create_app(store)) as client:

        def _held_request() -> None:
            answered["held"] = client.get(
                "/api/v1/projects/default/runs", headers=headers
            ).status_code

        def _capabilities() -> None:
            answered["capabilities"] = client.get("/api/v1/capabilities").status_code

        holder = threading.Thread(target=_held_request, daemon=True)
        other = threading.Thread(target=_capabilities, daemon=True)
        holder.start()
        try:
            assert entered.wait(_JOIN_TIMEOUT_SECONDS), f"the run list never called {held}"
            other.start()
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)
            assert not other.is_alive(), f"a request waited for {held}"
            assert "held" not in answered
        finally:
            release.set()
            holder.join(timeout=_JOIN_TIMEOUT_SECONDS)
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert answered == {"capabilities": 200, "held": 200}


def test_section_posts_racing_for_the_last_slot_never_pass_the_bound(
    any_store: ExecutionStore,
) -> None:
    """Counting the stored sections and then writing would let every post
    racing for the last free slot see it free. The store makes the count
    and the write one step, so exactly one racer is created."""
    now = datetime.now(timezone.utc)
    for index in range(MAX_SECTIONS - 1):
        any_store.upsert_setting(
            TEST_SECTIONS_NAMESPACE,
            f"Seeded{index:03d}",
            value='{"prefix": "tests/seeded/"}',
            updated_at=now,
            project=DEFAULT_PROJECT,
        )
    racers = 16
    statuses: list[int] = []

    with TestClient(create_app(any_store)) as client:

        def _post(index: int) -> None:
            section = {"name": f"Racer{index}", "prefix": f"tests/racer{index}"}
            response = client.post("/api/v1/projects/default/config/sections", json=section)
            statuses.append(response.status_code)

        errors = _with_frequent_thread_switches(
            lambda: _run_concurrently([partial(_post, index) for index in range(racers)])
        )

    assert errors == []
    assert sorted(statuses) == [201] + [422] * (racers - 1)
    assert (
        len(any_store.list_settings(TEST_SECTIONS_NAMESPACE, project=DEFAULT_PROJECT))
        == MAX_SECTIONS
    )


def test_section_posts_racing_for_the_last_slot_fill_each_project_independently(
    any_store: ExecutionStore,
) -> None:
    """Each project has sections of its own, so each has a last slot of its
    own. With both one short of the bound and posts racing into both, each
    project gets exactly one racer: a count across projects would find both
    full and create none, and one that did not make racers take turns would
    create more than one in a project."""
    now = datetime.now(timezone.utc)
    projects = [DEFAULT_PROJECT, "firmware"]
    any_store.create_project("firmware", created_at=now)
    for project in projects:
        for index in range(MAX_SECTIONS - 1):
            any_store.upsert_setting(
                TEST_SECTIONS_NAMESPACE,
                f"Seeded{index:03d}",
                value='{"prefix": "tests/seeded/"}',
                updated_at=now,
                project=project,
            )
    racers_per_project = 8
    statuses: dict[str, list[int]] = {project: [] for project in projects}

    with TestClient(create_app(any_store)) as client:

        def _post(project: str, index: int) -> None:
            section = {"name": f"Racer{index}", "prefix": f"tests/racer{index}"}
            response = client.post(f"/api/v1/projects/{project}/config/sections", json=section)
            statuses[project].append(response.status_code)

        errors = _with_frequent_thread_switches(
            lambda: _run_concurrently(
                [
                    partial(_post, project, index)
                    for index in range(racers_per_project)
                    for project in projects
                ]
            )
        )

    assert errors == []
    for project in projects:
        assert sorted(statuses[project]) == [201] + [422] * (racers_per_project - 1)
        assert (
            len(any_store.list_settings(TEST_SECTIONS_NAMESPACE, project=project)) == MAX_SECTIONS
        )
