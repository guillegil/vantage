"""Session report ingestion through `POST /api/v1/runs` and the heartbeat.

Most tests run `create_app` against an injected `InMemoryExecutionStore`;
the few that depend on how SQLite stores timestamps use the real adapter.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.metadata import (
    MAX_METADATA_ENTRIES,
    MAX_METADATA_KEY_CHARS,
    MAX_METADATA_VALUE_BYTES,
)
from vantage.core.ports.storage import MetadataEntry, MetadataFile
from vantage.service.app import create_app
from vantage.storage.memory import InMemoryExecutionStore
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _stored_metadata_entries, _stored_metadata_files


def _well_formed_report(run_id: str = "a" * 32) -> dict[str, Any]:
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


def _vcs_section(**overrides: Any) -> dict[str, Any]:
    """One well-formed `vcs` section."""
    section: dict[str, Any] = {
        "commit": "a" * 40,
        "branch": "main",
        "commit_subject": "a commit subject",
        "dirty": False,
        "root": "/repo",
    }
    section.update(overrides)
    return section


def _result_entry(node_id: str, **overrides: Any) -> dict[str, Any]:
    """One well-formed `results[]` entry.

    `overrides` adds an undeclared key or replaces a single field without
    repeating every other one.
    """
    entry: dict[str, Any] = {
        "node_id": node_id,
        "file_path": node_id.split("::", 1)[0],
        "class_name": None,
        "function_name": node_id.rsplit("::", 1)[-1],
        "param_id": None,
        "outcome": "passed",
        "duration": 0.0031,
        "started_at": "2026-08-18T09:14:02.481930+00:00",
        "finished_at": "2026-08-18T09:14:02.485012+00:00",
        "setup_outcome": "passed",
        "call_outcome": "passed",
        "teardown_outcome": "passed",
        "setup_duration": 0.0008,
        "call_duration": 0.0019,
        "teardown_duration": 0.0004,
        "worker_id": None,
    }
    entry.update(overrides)
    return entry


def _failing_result_entry(node_id: str, **overrides: Any) -> dict[str, Any]:
    """A `results[]` entry carrying every failure evidence field."""
    entry = _result_entry(node_id, outcome="failed", **overrides)
    entry.setdefault("failure_type", "AssertionError")
    entry.setdefault("failure_message", "AssertionError: assert 1200 == 1320")
    entry.setdefault("failure_message_truncated", False)
    entry.setdefault("failure_path", "tests/helpers/pricing.py")
    entry.setdefault("failure_lineno", 47)
    entry.setdefault("failure_repr", "AssertionError('assert 1200 == 1320')")
    entry.setdefault("failure_repr_truncated", False)
    entry.setdefault("traceback", "tests/test_orders.py:19: in test_total_includes_tax\n    ...")
    entry.setdefault("traceback_truncated", False)
    entry.setdefault("skip_reason", None)
    entry.setdefault("skip_reason_truncated", False)
    entry.setdefault("xfail_reason", None)
    entry.setdefault("xfail_reason_truncated", False)
    entry.setdefault("captured_stdout", "")
    entry.setdefault("captured_stdout_truncated", False)
    entry.setdefault("captured_stderr", None)
    entry.setdefault("captured_stderr_truncated", False)
    return entry


@pytest.fixture
def store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore()


@pytest.fixture
def client(store: InMemoryExecutionStore) -> TestClient:
    return TestClient(create_app(store))


@pytest.fixture
def sqlite_store(tmp_path: Path) -> Iterator[SqliteExecutionStore]:
    """The real SQLite adapter. Its `last_seen_at` guard compares stored TEXT
    lexicographically, so only this adapter -- not `InMemoryExecutionStore`,
    which compares `datetime` objects -- exposes un-normalized timestamps."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    yield adapter
    adapter.close()


@pytest.fixture
def sqlite_client(sqlite_store: SqliteExecutionStore) -> TestClient:
    return TestClient(create_app(sqlite_store))


def test_well_formed_report_is_stored_and_acknowledged(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    response = client.post("/api/v1/runs", json=_well_formed_report("a" * 32))

    assert response.status_code == 201
    body = response.json()
    assert body["run_id"] == "a" * 32
    assert body["status"] == "created"
    assert store.count_executions() == 1
    assert store.get_execution("a" * 32) is not None


def test_retried_report_is_idempotent(client: TestClient, store: InMemoryExecutionStore) -> None:
    report = _well_formed_report("b" * 32)

    first = client.post("/api/v1/runs", json=report)
    second = client.post("/api/v1/runs", json=report)

    assert first.status_code == 201
    assert second.status_code == 200
    body = second.json()
    assert body["run_id"] == "b" * 32
    assert body["status"] == "duplicate"
    assert store.count_executions() == 1


def test_report_with_vcs_section_persists_six_fields(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    report = _well_formed_report("2" + "0" * 31)
    report["vcs"] = _vcs_section()

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    execution = store.get_execution("2" + "0" * 31)
    assert execution is not None
    assert execution.vcs is not None
    assert execution.vcs.commit == "a" * 40
    assert execution.vcs.branch == "main"
    assert execution.vcs.commit_subject == "a commit subject"
    assert execution.vcs.dirty is False
    assert execution.vcs.root == "/repo"
    assert execution.vcs.commit_subject_truncated is False


def test_report_without_vcs_section_still_records_run(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A plugin that does not send `vcs` is a supported version skew."""
    report = _well_formed_report("2" + "1" * 31)
    assert "vcs" not in report

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert response.json()["status"] == "created"
    execution = store.get_execution("2" + "1" * 31)
    assert execution is not None
    assert execution.vcs is None


@pytest.mark.parametrize("path", ["/runs", "/api/runs"])
def test_unversioned_path_is_refused(client: TestClient, path: str) -> None:
    response = client.post(path, json=_well_formed_report())

    assert response.status_code == 404


def test_report_carrying_results_stores_them_with_the_run(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A report carrying N results stores N result rows with the run, and
    each reads back with the outcome it was sent with.
    """
    report = _well_formed_report("c" * 32)
    report["results"] = [
        _result_entry("packages/vantage/tests/test_a.py::test_one"),
        _result_entry("packages/vantage/tests/test_a.py::test_two", outcome="failed"),
    ]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.count_executions() == 1
    assert store.count_results() == 2
    stored = {result.identity.node_id: result for result in store.get_results("c" * 32)}
    assert stored["packages/vantage/tests/test_a.py::test_one"].outcome == "passed"
    assert stored["packages/vantage/tests/test_a.py::test_two"].outcome == "failed"


@pytest.mark.parametrize("results_value", [None, []], ids=["null", "empty-list"])
def test_report_with_null_or_empty_results_section_writes_no_result_rows(
    client: TestClient, store: InMemoryExecutionStore, results_value: list[Any] | None
) -> None:
    """`results: null` and `results: []` both record the run and write zero
    result rows -- not an error.
    """
    run_id = "d" * 32 if results_value is None else "d" * 31 + "e"
    report = _well_formed_report(run_id)
    report["results"] = results_value

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.count_executions() == 1
    assert store.count_results() == 0


def test_replayed_report_with_results_does_not_duplicate_them(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Replaying an already-stored report leaves the same result rows in
    place and is acknowledged, never rejected."""
    report = _well_formed_report("f" * 32)
    report["results"] = [_result_entry("packages/vantage/tests/test_b.py::test_one")]

    first = client.post("/api/v1/runs", json=report)
    second = client.post("/api/v1/runs", json=report)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert store.count_results() == 1


def test_unknown_result_key_is_tolerated_and_named_deduplicated_in_ignored(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """An unknown key on a *result* is tolerated (`extra="allow"`), and its
    **name** is reported back deduplicated as `results[].<name>` -- one
    entry for two results carrying the same unknown key, never a per-index
    path."""
    report = _well_formed_report("1" + "a" * 31)
    report["results"] = [
        _result_entry("packages/vantage/tests/test_c.py::test_one", tags=["slow"]),
        _result_entry("packages/vantage/tests/test_c.py::test_two", tags=["slow"]),
    ]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    body = response.json()
    assert body["ignored"] == ["results[].tags"]
    assert store.count_results() == 2


def test_an_older_plugin_omitting_failure_fields_still_stores_run_and_results(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A report with no failure evidence keys at all still stores one run
    and its results, every failure field absent."""
    report = _well_formed_report("5" + "0" * 31)
    report["results"] = [_result_entry("packages/vantage/tests/test_d.py::test_one")]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.count_executions() == 1
    assert store.count_results() == 1
    [result] = store.get_results("5" + "0" * 31)
    assert result.failure is None
    assert result.captured.stdout is None
    assert result.captured.stderr is None


def test_a_newer_plugins_failure_evidence_fields_are_persisted(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A report carrying failure evidence fields round-trips through
    storage."""
    report = _well_formed_report("5" + "2" * 31)
    report["results"] = [_failing_result_entry("packages/vantage/tests/test_g.py::test_one")]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    [result] = store.get_results("5" + "2" * 31)
    assert result.failure is not None
    assert result.failure.failure_type == "AssertionError"
    assert result.failure.failure_message == "AssertionError: assert 1200 == 1320"
    assert (
        result.failure.traceback == "tests/test_orders.py:19: in test_total_includes_tax\n    ..."
    )
    assert result.captured.stdout == ""
    assert result.captured.stderr is None


def test_an_older_server_tolerates_unrecognized_failure_evidence_keys(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """An unrecognized failure evidence key on a result is accepted under
    `ResultReport`'s `extra="allow"`, and its name surfaces in
    `Acknowledgement.ignored`."""
    report = _well_formed_report("5" + "1" * 31)
    report["results"] = [
        _result_entry(
            "packages/vantage/tests/test_e.py::test_one",
            outcome="failed",
            failure_context_extra="unexpected-future-field",
        )
    ]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    body = response.json()
    assert body["ignored"] == ["results[].failure_context_extra"]
    assert store.count_results() == 1


def test_result_report_param_id_and_duration_survive_the_pydantic_hop() -> None:
    """`param_id: ""` and `param_id: null` on the wire arrive as distinct
    Python values, and a duration of `0.0` survives as `0.0` -- no
    falsy-to-`None` coercion."""
    from vantage.service.schemas import ResultReport

    empty_param = ResultReport.model_validate(
        _result_entry("packages/vantage/tests/test_result.py::test_x[]", param_id="")
    )
    absent_param = ResultReport.model_validate(
        _result_entry("packages/vantage/tests/test_result.py::test_y", param_id=None)
    )
    zero_duration = ResultReport.model_validate(
        _result_entry("packages/vantage/tests/test_result.py::test_z", duration=0.0)
    )

    assert empty_param.param_id == ""
    assert absent_param.param_id is None
    assert empty_param.param_id != absent_param.param_id
    assert zero_duration.duration == 0.0


def test_an_older_run_with_a_non_utc_offset_does_not_roll_back_the_catalogue(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore
) -> None:
    """`last_seen_at` is TEXT compared with `MAX`, which is only correct when
    every stored value has the same UTC offset. Un-normalized,
    `'...T12:00:00+02:00'` (10:00 UTC, earlier) sorts after
    `'...T11:00:00+00:00'` (11:00 UTC) and would wrongly advance the
    catalogue entry."""
    node_id = "packages/vantage/tests/test_utc.py::test_guard"

    first_report = _well_formed_report("7" * 32)
    first_report["run"]["started_at"] = "2026-08-18T11:00:00+00:00"  # 11:00 UTC
    first_report["results"] = [_result_entry(node_id)]
    sqlite_client.post("/api/v1/runs", json=first_report)

    second_report = _well_formed_report("8" * 32)
    second_report["run"]["started_at"] = "2026-08-18T12:00:00+02:00"  # 10:00 UTC, earlier
    second_report["results"] = [_result_entry(node_id)]
    sqlite_client.post("/api/v1/runs", json=second_report)

    entry = sqlite_store.get_catalogue_entry(node_id)
    assert entry is not None
    assert entry.last_seen_at == datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc)
    assert entry.last_seen_run_id == "7" * 32


def test_non_utc_and_naive_timestamps_normalize_to_one_utc_form(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore
) -> None:
    """Run and result timestamps are both normalized to UTC before they
    reach the store, checked together so the two paths cannot diverge. An
    aware value converts to its UTC equivalent; a naive value is taken as
    UTC."""
    node_id = "packages/vantage/tests/test_utc.py::test_normalizes"
    report = _well_formed_report("9" * 32)
    report["run"]["started_at"] = "2026-08-18T13:00:00+02:00"  # 11:00 UTC
    report["run"]["finished_at"] = "2026-08-18T13:00:05"  # naive -- interpreted as UTC
    report["results"] = [
        _result_entry(
            node_id,
            started_at="2026-08-18T13:00:01+02:00",  # 11:00:01 UTC
            finished_at="2026-08-18T13:00:02",  # naive -- interpreted as UTC
        )
    ]

    response = sqlite_client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    raw_started_at = sqlite_store._conn.execute(
        "SELECT started_at FROM run WHERE id = ?", ("9" * 32,)
    ).fetchone()[0]
    assert raw_started_at == "2026-08-18T11:00:00+00:00"

    execution = sqlite_store.get_execution("9" * 32)
    assert execution is not None
    assert execution.started_at == datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc)
    assert execution.finished_at == datetime(2026, 8, 18, 13, 0, 5, tzinfo=timezone.utc)

    [result] = sqlite_store.get_results("9" * 32)
    assert result.started_at == datetime(2026, 8, 18, 11, 0, 1, tzinfo=timezone.utc)
    assert result.finished_at == datetime(2026, 8, 18, 13, 0, 2, tzinfo=timezone.utc)


def test_timestamps_at_the_edge_of_the_utc_range_are_accepted(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore
) -> None:
    """Only a timestamp whose UTC form leaves years 1-9999 is refused; one
    that lands exactly on either end converts and is stored."""
    report = _well_formed_report("9" * 31 + "a")
    report["run"]["started_at"] = "0001-01-01T01:00:00+01:00"
    report["run"]["finished_at"] = "9999-12-31T18:59:59-05:00"

    response = sqlite_client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    execution = sqlite_store.get_execution("9" * 31 + "a")
    assert execution is not None
    assert execution.started_at == datetime(1, 1, 1, tzinfo=timezone.utc)
    assert execution.finished_at == datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize("bound", [2**63 - 1, -(2**63)], ids=["int64_max", "int64_min"])
def test_integers_at_the_signed_64_bit_bounds_are_stored(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore, bound: int
) -> None:
    """The integer bound is SQLite's own range: its two ends are stored and
    read back unchanged."""
    run_id = "9" * 31 + ("b" if bound > 0 else "c")
    report = _well_formed_report(run_id)
    report["run"]["exit_status"] = bound
    report["results"] = [_failing_result_entry("tests/test_a.py::test_one", failure_lineno=bound)]

    response = sqlite_client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    execution = sqlite_store.get_execution(run_id)
    assert execution is not None
    assert execution.exit_status == bound
    [result] = sqlite_store.get_results(run_id)
    assert result.failure is not None
    assert result.failure.failure_lineno == bound


# --- lone surrogates ----------------------------------------------------------

# pytest builds node ids and exception text from `surrogateescape`-decoded
# file names, and `json.dumps` escapes the resulting lone surrogate into valid
# JSON, so a real plugin sends these.
_LONE = "caf\udce9"
_REPLACED = "caf\ufffd"
_NODE_ID = f"tests/{_LONE}/test_a.py::test_one"


def _surrogate_report(field: str) -> dict[str, Any]:
    report = _well_formed_report("5" + "e" * 31)
    result = _result_entry("tests/test_a.py::test_one")
    if field == "node_id":
        result = _result_entry(_NODE_ID)
    elif field == "run.interrupt_reason":
        report["run"]["interrupt_reason"] = _LONE
    elif field.startswith("vcs."):
        report["vcs"] = _vcs_section(**{field.removeprefix("vcs."): _LONE})
    elif field == "metadata.path":
        report["metadata"] = _metadata_section(
            _metadata_file(path=f"{_LONE}.json", content="{}", keys=["k"])
        )
    elif field == "metadata.key":
        report["metadata"] = _metadata_section(_metadata_file(content="{}", keys=[_LONE]))
    else:
        result[field] = _LONE
    report["results"] = [result]
    return report


def _stored_surrogate_field(store: Any, field: str) -> object:
    run_id = "5" + "e" * 31
    execution = store.get_execution(run_id)
    [result] = store.get_results(run_id)
    if field == "node_id":
        return (result.identity.node_id, result.identity.file_path)
    if field == "run.interrupt_reason":
        return execution.interrupt_reason
    if field.startswith("vcs."):
        return getattr(execution.vcs, field.removeprefix("vcs."))
    if field == "metadata.path":
        return {file.source_file for file in _stored_metadata_files(store, run_id)}
    if field == "metadata.key":
        return {entry.key for entry in _stored_metadata_entries(store, run_id)}
    if field == "worker_id":
        return result.worker_id
    if field == "captured_stdout":
        return result.captured.stdout
    return getattr(result.failure, field)


_SURROGATE_CASES: dict[str, object] = {
    "node_id": (f"tests/{_REPLACED}/test_a.py::test_one", f"tests/{_REPLACED}/test_a.py"),
    "worker_id": _REPLACED,
    "failure_message": _REPLACED,
    "skip_reason": _REPLACED,
    "xfail_reason": _REPLACED,
    "captured_stdout": _REPLACED,
    "run.interrupt_reason": _REPLACED,
    "vcs.branch": _REPLACED,
    "vcs.commit_subject": _REPLACED,
    "metadata.path": {f"{_REPLACED}.json"},
    "metadata.key": {_REPLACED},
}


@pytest.fixture(params=["memory", "sqlite"])
def any_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    if request.param == "memory":
        yield InMemoryExecutionStore()
        return
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    yield adapter
    adapter.close()


@pytest.mark.parametrize(("field", "expected"), _SURROGATE_CASES.items(), ids=_SURROGATE_CASES)
def test_a_lone_surrogate_in_any_string_is_stored_as_the_replacement_character(
    any_store: Any, field: str, expected: object
) -> None:
    """A lone surrogate cannot be encoded as UTF-8, so the server replaces it
    with U+FFFD before validation instead of failing the whole session at
    the first encode. Both adapters store the same text."""
    client = TestClient(create_app(any_store))

    response = client.post(
        "/api/v1/runs",
        content=json.dumps(_surrogate_report(field)).encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 201
    assert _stored_surrogate_field(any_store, field) == expected


def test_a_lone_surrogate_in_a_key_is_replaced_too(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Keys are client text as well: an unknown result key carrying a lone
    surrogate is tolerated like any other unknown key, not refused."""
    report = _well_formed_report("5" + "f" * 31)
    report["results"] = [_result_entry("tests/test_a.py::test_one", **{_LONE: "x"})]

    response = client.post(
        "/api/v1/runs",
        content=json.dumps(report).encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 201
    assert response.json()["ignored"] == ["results[].<unnamed>"]


# --- heartbeat endpoint -------------------------------------------------------


def test_heartbeat_advances_last_contact_for_an_accepted_start_write(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    report = _well_formed_report("2" + "a" * 31)
    report["run"]["finished_at"] = None
    report["run"]["exit_status"] = None
    client.post("/api/v1/runs", json=report)
    run_id = report["run"]["id"]
    before = store._last_contact[run_id]  # noqa: SLF001

    response = client.post(f"/api/v1/runs/{run_id}/heartbeat")

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "status": "acknowledged"}
    assert store._last_contact[run_id] > before  # noqa: SLF001


def test_heartbeat_cannot_touch_finish_fields(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A heartbeat for a finished run leaves `finished_at`, `exit_status`,
    `interrupted` and `interrupt_reason` exactly as recorded."""
    report = _well_formed_report("3" + "a" * 31)
    client.post("/api/v1/runs", json=report)
    run_id = report["run"]["id"]
    before = store.get_execution(run_id)
    assert before is not None

    response = client.post(f"/api/v1/runs/{run_id}/heartbeat")

    assert response.status_code == 200
    after = store.get_execution(run_id)
    assert after is not None
    assert after.finished_at == before.finished_at
    assert after.exit_status == before.exit_status
    assert after.interrupted == before.interrupted
    assert after.interrupt_reason == before.interrupt_reason


def test_heartbeat_for_a_known_run_with_a_later_recorded_contact_is_200_not_404(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """The 404 comes from `get_execution`, not from a no-op update: a known
    run whose stored contact is already ahead of this beat makes
    `touch_last_contact` return `False`, yet the answer is still 200.
    `_last_contact` is set into the future directly so the beat is
    guaranteed earlier without a timing race.
    """
    report = _well_formed_report("4" + "a" * 31)
    report["run"]["finished_at"] = None
    report["run"]["exit_status"] = None
    client.post("/api/v1/runs", json=report)
    run_id = report["run"]["id"]
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    store._last_contact[run_id] = far_future  # noqa: SLF001

    response = client.post(f"/api/v1/runs/{run_id}/heartbeat")

    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "status": "acknowledged"}
    # The monotonic guard rejected the earlier beat: the stored contact is
    # unchanged, yet the response is still 200 -- a rowcount-based 404 would
    # have answered 404 here.
    assert store._last_contact[run_id] == far_future  # noqa: SLF001


# --- `app.state.grace_period` -------------------------------------------------


def test_create_app_defaults_grace_period_to_900_seconds() -> None:
    app = create_app(InMemoryExecutionStore())

    assert app.state.grace_period == 900.0


def test_create_app_exposes_the_configured_grace_period() -> None:
    app = create_app(InMemoryExecutionStore(), grace_period_seconds=123.0)

    assert app.state.grace_period == 123.0


# --- capability advertisement -------------------------------------------------


def test_capabilities_endpoint_advertises_the_session_lifecycle(client: TestClient) -> None:
    """`GET /api/v1/capabilities` answers `{"session_lifecycle": true}` -- a
    capability flag, not a version string."""
    response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {"session_lifecycle": True}


def test_capabilities_endpoint_is_not_mounted_unversioned(client: TestClient) -> None:
    """Mounted under `/api/v1` and nowhere else, like the run routes."""
    response = client.get("/capabilities")

    assert response.status_code == 404


# --- metadata section ingestion -----------------------------------------------


def _metadata_file(
    path: str = "config/firmware.json",
    *,
    format: str = "json",  # noqa: A002
    status: str = "captured",
    keys: list[str] | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "format": format,
        "status": status,
        "keys": [] if keys is None else keys,
        "content": content,
    }


def _metadata_section(*files: dict[str, Any]) -> dict[str, Any]:
    return {"declaration": "vantage-metadata.json", "files": list(files)}


_VALUE_TOO_LARGE = "x" * (MAX_METADATA_VALUE_BYTES + 1)

# Every metadata outcome, one case each: (file, expected file row or None,
# expected entry rows). A server-side shape reject expects no rows at all --
# the file is dropped in its entirety.
_TAXONOMY_CASES: dict[str, tuple[dict[str, Any], MetadataFile | None, frozenset[MetadataEntry]]] = {
    "not_found": (
        _metadata_file(status="not_found", content=None, keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="not_found"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
    "path_rejected": (
        _metadata_file(status="path_rejected", content=None, keys=["firmware_version"]),
        MetadataFile(
            source_file="config/firmware.json", content_type="json", status="path_rejected"
        ),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
    "too_large": (
        _metadata_file(status="too_large", content=None, keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="too_large"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
    "not_text": (
        _metadata_file(status="not_text", content=None, keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="not_text"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
    "unreadable": (
        _metadata_file(status="unreadable", content=None, keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="unreadable"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
    "over_budget": (
        _metadata_file(status="over_budget", content=None, keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="over_budget"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
    "malformed": (
        _metadata_file(content="not json at all", keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="malformed"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
    "absent": (
        _metadata_file(content=json.dumps({"other_key": "x"}), keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="captured"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="absent",
                )
            }
        ),
    ),
    "not_scalar": (
        _metadata_file(
            content=json.dumps({"firmware_version": {"nested": True}}),
            keys=["firmware_version"],
        ),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="captured"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="not_scalar",
                )
            }
        ),
    ),
    "value_too_large": (
        _metadata_file(
            content=json.dumps({"firmware_version": _VALUE_TOO_LARGE}),
            keys=["firmware_version"],
        ),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="captured"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="value_too_large",
                )
            }
        ),
    ),
    "server_side_shape_reject": (
        _metadata_file(
            path="../escape.json",
            content=json.dumps({"firmware_version": "2.1"}),
            keys=["firmware_version"],
        ),
        None,
        frozenset(),
    ),
    "server_side_shape_reject_windows": (
        _metadata_file(
            path="..\\escape.json", status="path_rejected", content=None, keys=["firmware_version"]
        ),
        None,
        frozenset(),
    ),
    "captured_without_content": (
        _metadata_file(content=None, keys=["firmware_version"]),
        MetadataFile(source_file="config/firmware.json", content_type="json", status="malformed"),
        frozenset(
            {
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.json",
                    status="source_unavailable",
                )
            }
        ),
    ),
}


# `run_id` is derived from each case's position, never `hash()` -- Python
# randomizes string hashing per-process, which would make the derived id
# (and its hex-pattern validity) nondeterministic across runs.
_TAXONOMY_PARAMS = [
    (f"7{index:031x}", file_report, expected_file, expected_entries)
    for index, (file_report, expected_file, expected_entries) in enumerate(_TAXONOMY_CASES.values())
]


@pytest.mark.parametrize(
    ("run_id", "file_report", "expected_file", "expected_entries"),
    _TAXONOMY_PARAMS,
    ids=list(_TAXONOMY_CASES.keys()),
)
def test_each_metadata_outcome_records_the_exact_file_and_key_status(
    client: TestClient,
    store: InMemoryExecutionStore,
    run_id: str,
    file_report: dict[str, Any],
    expected_file: MetadataFile | None,
    expected_entries: frozenset[MetadataEntry],
) -> None:
    """Each outcome records the exact `(file.status, key.status)` pair, not
    merely an ingestion that did not crash."""
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(file_report)

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert _stored_metadata_files(store, run_id) == (
        frozenset({expected_file}) if expected_file is not None else frozenset()
    )
    assert _stored_metadata_entries(store, run_id) == expected_entries


def test_a_declared_key_within_bound_is_captured_whole(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    run_id = "9" + "5" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(content=json.dumps({"firmware_version": "2.1"}), keys=["firmware_version"])
    )

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert _stored_metadata_entries(store, run_id) == frozenset(
        {
            MetadataEntry(
                key="firmware_version",
                value="2.1",
                source_file="config/firmware.json",
                status="captured",
            )
        }
    )


def test_a_report_with_no_metadata_section_still_records_its_run(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    run_id = "9" + "6" * 31
    report = _well_formed_report(run_id)
    assert "metadata" not in report

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.get_execution(run_id) is not None
    assert _stored_metadata_files(store, run_id) == frozenset()
    assert _stored_metadata_entries(store, run_id) == frozenset()


def test_a_yaml_declared_document_is_parsed(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    run_id = "9" + "7" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(
            path="config/firmware.yaml",
            format="yaml",
            content='firmware_version: "2.1"\n',
            keys=["firmware_version"],
        )
    )

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert _stored_metadata_entries(store, run_id) == frozenset(
        {
            MetadataEntry(
                key="firmware_version",
                value="2.1",
                source_file="config/firmware.yaml",
                status="captured",
            )
        }
    )


def test_an_unsupported_format_is_treated_as_malformed(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """`toml` is a storable `run_metadata_file.content_type` that
    `metadata_parse.parse` does not implement, so it is recorded as
    `malformed`, like a broken document."""
    run_id = "9" + "8" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(format="toml", content="firmware_version = 2.1", keys=["firmware_version"])
    )

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert _stored_metadata_files(store, run_id) == frozenset(
        {MetadataFile(source_file="config/firmware.json", content_type="toml", status="malformed")}
    )


_UNUSABLE_DOCUMENTS = {
    "json_lone_surrogate_escape": ("json", '{"firmware_version": "x\\ud800"}'),
    "yaml_lone_surrogate_escape": ("yaml", 'firmware_version: "x\\udc80"\n'),
}


@pytest.mark.parametrize(
    ("content_type", "content"), _UNUSABLE_DOCUMENTS.values(), ids=_UNUSABLE_DOCUMENTS.keys()
)
def test_a_declared_document_the_server_cannot_store_still_records_the_run(
    any_store: Any, content_type: str, content: str
) -> None:
    """The metadata section travels with the start report and the finish
    report alike, so a document that crashed the parser would lose every
    write of the session. It is recorded `malformed` instead."""
    run_id = "6" + "1" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(format=content_type, content=content, keys=["firmware_version"])
    )

    response = TestClient(create_app(any_store)).post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert _stored_metadata_files(any_store, run_id) == frozenset(
        {
            MetadataFile(
                source_file="config/firmware.json", content_type=content_type, status="malformed"
            )
        }
    )


def test_a_near_cap_yaml_document_is_stored_too_large_rather_than_parsed(
    any_store: Any,
) -> None:
    """Composing YAML costs seconds per megabyte on the event loop every
    route shares; a document only a non-plugin client could send is
    refused by size, and the run is still recorded."""
    run_id = "6" + "3" * 31
    report = _well_formed_report(run_id)
    content = "k: [" + "1," * 480_000 + "1]\n"
    report["metadata"] = _metadata_section(
        _metadata_file(path="config/big.yaml", format="yaml", content=content, keys=["k"])
    )

    response = TestClient(create_app(any_store)).post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert _stored_metadata_files(any_store, run_id) == frozenset(
        {MetadataFile(source_file="config/big.yaml", content_type="yaml", status="too_large")}
    )


def test_the_server_stores_at_most_the_metadata_entry_bound(any_store: Any) -> None:
    """The plugin refuses a declaration over the entry and key bounds, but
    any HTTP client can report here; the excess is dropped, never the
    session."""
    run_id = "6" + "4" * 31
    report = _well_formed_report(run_id)
    keys = [f"key_{index}" for index in range(MAX_METADATA_ENTRIES + 50)]
    report["metadata"] = _metadata_section(
        _metadata_file(content="{}", keys=keys[:100]),
        _metadata_file(path="config/b.json", content="{}", keys=keys[100:]),
        _metadata_file(
            path="config/c.json", content="{}", keys=["k" * (MAX_METADATA_KEY_CHARS + 1)]
        ),
    )

    response = TestClient(create_app(any_store)).post("/api/v1/runs", json=report)

    assert response.status_code == 201
    stored = _stored_metadata_entries(any_store, run_id)
    assert {entry.key for entry in stored} == set(keys[:MAX_METADATA_ENTRIES])
    assert len(_stored_metadata_files(any_store, run_id)) == 3


def test_a_json_declared_number_is_stored_as_the_text_the_file_holds(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A metadata filter compares strings, so `2.10` must not be stored as
    `2.1`."""
    run_id = "6" + "2" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(content='{"firmware_version": 2.10}', keys=["firmware_version"])
    )

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    [entry] = _stored_metadata_entries(store, run_id)
    assert entry.value == "2.10"


def test_a_json_integer_past_the_digit_limit_still_records_the_run(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Converting a literal over the interpreter's int digit limit raises,
    and a file under the plugin's per-file bound can hold one. Kept as text,
    it is only a value too large to store."""
    run_id = "6" + "5" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(content='{"build": 1' + "0" * 4999 + "}", keys=["build"])
    )

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    [entry] = _stored_metadata_entries(store, run_id)
    assert (entry.value, entry.status) == (None, "value_too_large")


# --- hostile client-chosen text -----------------------------------------------


def test_a_quoting_shaped_declared_key_round_trips_byte_identically(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """A declared key containing quote characters is stored and read back
    through the port intact, never escaped or normalised."""
    run_id = "8" + "0" * 31
    key = 'He said "hi", didn\'t he?'
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(content=json.dumps({key: "value"}), keys=[key])
    )

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert _stored_metadata_entries(store, run_id) == frozenset(
        {
            MetadataEntry(
                key=key, value="value", source_file="config/firmware.json", status="captured"
            )
        }
    )


def test_a_crlf_shaped_metadata_key_never_appears_unescaped_in_a_rejection_body(
    client: TestClient,
) -> None:
    """An unknown field in the `metadata` section (`extra="forbid"`) is
    client-chosen text; `safe_segment` keeps it out of the rejection body."""
    run_id = "8" + "1" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = {
        "declaration": "vantage-metadata.json",
        "files": [],
        "\r\n</script>\r\nX-Injected: 1": "hostile",
    }

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 422
    body = response.text
    assert "\r\n</script>" not in body
    assert "X-Injected" not in body
