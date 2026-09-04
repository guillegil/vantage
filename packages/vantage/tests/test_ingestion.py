"""RQ-41: session report ingestion -- happy path and idempotent replay.

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, per design.md's ordering note: B does not depend on
A2 (schema/SQLite adapter, PR2-PR5). Wiring the real `SqliteExecutionStore`
in here would reintroduce a dependency this slice is explicitly free of.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.metadata import MAX_METADATA_VALUE_BYTES
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
    """One well-formed `vcs` section (design.md D47's wire shape)."""
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
    """One well-formed `results[]` entry (design.md D15 interface example).

    `overrides` lets a test carry an extra, undeclared key (task 4.4) or
    override a single field (task 4.5's `param_id`/`duration`) without
    repeating every other field.
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
    """A `results[]` entry carrying failure evidence -- the wire shape
    design.md's "Interfaces / Contracts" example gives (D75), for the
    newer-plugin ingestion scenarios."""
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
    """The real SQLite adapter (tasks 5.9/5.10): the D20 `MAX` monotonicity
    guard is a lexicographic comparison of stored TEXT, so only this adapter
    -- not `InMemoryExecutionStore`, which compares real `datetime` objects
    and is already correct -- can reproduce the un-normalized-timestamp bug
    (Engram observation 62)."""
    adapter = SqliteExecutionStore(tmp_path / "store" / "vantage.db")
    yield adapter
    adapter.close()


@pytest.fixture
def sqlite_client(sqlite_store: SqliteExecutionStore) -> TestClient:
    return TestClient(create_app(sqlite_store))


@pytest.mark.req(id="RQ-41")
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


@pytest.mark.req(id="RQ-41")
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
    """design.md D47/D48, task 4.6. *(Scenario: A report carrying a vcs
    section persists its six fields, `session-ingestion`)*."""
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
    """design.md D47, task 4.7 -- the supported skew case for a plugin that
    predates this change. *(Scenario: A report with no vcs section still
    records its run, `session-ingestion`)*."""
    report = _well_formed_report("2" + "1" * 31)
    assert "vcs" not in report

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert response.json()["status"] == "created"
    execution = store.get_execution("2" + "1" * 31)
    assert execution is not None
    assert execution.vcs is None


def test_vcs_section_accepted_without_capability_check(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """design.md D47, task 4.8 -- `routes/capabilities.py` is unchanged, no
    flag ever advertises a vcs-related capability, and none is required
    before this section is accepted. *(Scenario: The endpoint accepts a vcs
    section without any capability check, `session-ingestion`)*."""
    report = _well_formed_report("2" + "2" * 31)
    report["vcs"] = _vcs_section()

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    execution = store.get_execution("2" + "2" * 31)
    assert execution is not None
    assert execution.vcs is not None


@pytest.mark.req(id="RQ-41")
@pytest.mark.parametrize("path", ["/runs", "/api/runs"])
def test_unversioned_path_is_refused(client: TestClient, path: str) -> None:
    response = client.post(path, json=_well_formed_report())

    assert response.status_code == 404


@pytest.mark.req(id="RQ-41")
def test_report_carrying_results_stores_them_with_the_run(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """RQ-41.1: a report carrying N results stores N result rows with the run.

    Also proves the results survive the round trip, not just their count --
    two entries with different outcomes read back with the outcomes they
    were sent with.
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


@pytest.mark.req(id="RQ-41")
@pytest.mark.parametrize("results_value", [None, []], ids=["null", "empty-list"])
def test_report_with_null_or_empty_results_section_writes_no_result_rows(
    client: TestClient, store: InMemoryExecutionStore, results_value: list[Any] | None
) -> None:
    """RQ-41.1, D15: `results: null` and `results: []` both record the run
    and write zero result rows -- the supported plugin/server skew case, not
    an error.
    """
    run_id = "d" * 32 if results_value is None else "d" * 31 + "e"
    report = _well_formed_report(run_id)
    report["results"] = results_value

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.count_executions() == 1
    assert store.count_results() == 0


@pytest.mark.req(id="RQ-41")
@pytest.mark.parametrize("results_value", [None, []], ids=["null", "empty-list"])
def test_session_report_accepts_a_null_or_empty_results_section(
    results_value: list[Any] | None,
) -> None:
    """The schema-level RED for the same scenario: `SessionReport.results`
    must exist and round-trip the sent value, rather than being silently
    dropped by the envelope's `extra="ignore"` (which is what would happen
    today, before `results` is a declared field)."""
    from vantage.service.schemas import SessionReport

    payload = SessionReport.model_validate({**_well_formed_report(), "results": results_value})

    assert payload.results == results_value


@pytest.mark.req(id="RQ-41")
def test_replayed_report_with_results_does_not_duplicate_them(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """RQ-41.2, D19 layer 3: replaying an already-stored report leaves the
    same result rows in place and is acknowledged, never rejected."""
    report = _well_formed_report("f" * 32)
    report["results"] = [_result_entry("packages/vantage/tests/test_b.py::test_one")]

    first = client.post("/api/v1/runs", json=report)
    second = client.post("/api/v1/runs", json=report)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert store.count_results() == 1


@pytest.mark.req(id="RQ-41")
def test_unknown_result_key_is_tolerated_and_named_deduplicated_in_ignored(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """D15: an unknown key on a *result* is tolerated (`extra="allow"`), and
    its **name** is reported back deduplicated as `results[].<name>` -- one
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
    """session-ingestion → An older plugin omitting the fields still stores
    its run and results (task 6.10): a report shaped exactly like every
    pre-`failure-capture` report -- no failure-evidence keys at all -- still
    stores one run and its results, every failure field absent."""
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
    """session-ingestion → A newer plugin's failure-evidence fields are
    persisted (task 6.11): a report carrying the new fields round-trips
    through storage."""
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
    """session-ingestion → An older server tolerates a newer plugin's
    failure-evidence fields (task 6.12): an unrecognized key under
    `ResultReport`'s existing `extra="allow"` tolerance is accepted and its
    name surfaces, deduplicated, in `Acknowledgement.ignored`."""
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


def test_a_report_carrying_failure_evidence_within_the_cap_is_accepted_normally(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """session-ingestion → A report carrying failure evidence within the cap
    is accepted normally (task 6.14): one run row, results stored with their
    fields, response acknowledges."""
    report = _well_formed_report("5" + "3" * 31)
    report["results"] = [_failing_result_entry("packages/vantage/tests/test_h.py::test_one")]

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert store.count_executions() == 1
    assert store.count_results() == 1


@pytest.mark.req(id="RQ-9")
def test_result_report_param_id_and_duration_survive_the_pydantic_hop() -> None:
    """The Pydantic hop of the four-hop `""`-vs-`None` guard (design.md D18):
    `param_id: ""` and `param_id: null` on the wire must arrive as distinct
    Python values. No `min_length=1`, no falsy-to-`None` coercion -- the same
    rule applies to a duration of `0.0`, which must survive as `0.0`, not be
    coerced to `None`."""
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


@pytest.mark.req(id="RQ-13")
def test_an_older_run_with_a_non_utc_offset_does_not_roll_back_the_catalogue(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore
) -> None:
    """Reproduces the Phase 3 finding (Engram observation 62): `last_seen_at`
    is TEXT and D20's `MAX` guard compares it lexicographically, which is
    only correct once every writer normalizes to the same UTC offset. Before
    the boundary normalizes, `'...T12:00:00+02:00'` (10:00 UTC, genuinely
    earlier) sorts AFTER `'...T11:00:00+00:00'` (11:00 UTC) as a string and
    rolls the catalogue forward -- exactly what D20 exists to prevent."""
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


@pytest.mark.req(id="RQ-30")
def test_non_utc_and_naive_timestamps_normalize_to_one_utc_form(
    sqlite_client: TestClient, sqlite_store: SqliteExecutionStore
) -> None:
    """D-addendum (2026-08-18): one helper normalizes every timestamp before
    it reaches the store -- `run.started_at`/`finished_at` and a result's
    `started_at`/`finished_at` alike, proven in one test on purpose so the
    two are never allowed to diverge onto separate paths. An aware value
    converts to its UTC equivalent; a naive value is interpreted as UTC."""
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


# --- Phase 4: heartbeat endpoint (design.md D33, task 4.2/4.3) --------------


@pytest.mark.req(id="RQ-44")
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


@pytest.mark.req(id="RQ-44")
def test_heartbeat_cannot_touch_finish_fields(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """design.md D33, task 4.3: a heartbeat for a run whose finish is already
    recorded leaves `finished_at`, `exit_status`, `interrupted` and
    `interrupt_reason` exactly as recorded -- the body is `{}` and read by
    nothing, so there is no field to smuggle a change through."""
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


@pytest.mark.req(id="RQ-44")
def test_heartbeat_for_a_known_run_with_a_later_recorded_contact_is_200_not_404(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """W1: the 404 is resolved by `get_execution`, never by a zero-rowcount
    update -- the one case that distinguishes the two implementations is a
    *known* run whose stored `last_contact_at` is already ahead of this
    beat, so `touch_last_contact` itself returns `False`. Every other
    heartbeat test's incoming beat is strictly later than the stored
    contact, so `rowcount` would also be 1 there and could not catch a
    regression to the wrong implementation. Setting `_last_contact` directly
    into the future, rather than issuing two real heartbeats, is what
    guarantees the beat under test is provably earlier without a timing race.
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


# --- Phase 4: `app.state.grace_period` (design.md D34, task 4.14) ----------


def test_create_app_defaults_grace_period_to_900_seconds() -> None:
    app = create_app(InMemoryExecutionStore())

    assert app.state.grace_period == 900.0


def test_create_app_exposes_the_configured_grace_period() -> None:
    app = create_app(InMemoryExecutionStore(), grace_period_seconds=123.0)

    assert app.state.grace_period == 123.0


# --- Capability advertisement (design decisions D38-D40, tasks 1.1/1.2) -----


def test_capabilities_endpoint_advertises_the_session_lifecycle(client: TestClient) -> None:
    """D38: the server advertises exactly one capability, not a version
    string -- `GET /api/v1/capabilities` answers `{"session_lifecycle":
    true}`, the one explicit positive answer a client's fail-closed check
    (D40) may treat as permission."""
    response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {"session_lifecycle": True}


def test_capabilities_endpoint_is_not_mounted_unversioned(client: TestClient) -> None:
    """Mounted under `/api/v1` and nowhere else -- the same absence rule
    `app.py`'s own docstring already states for the run routes (RQ-41's
    third criterion)."""
    response = client.get("/capabilities")

    assert response.status_code == 404


# --- Phase 9: metadata section ingestion (design.md D96-D98) ---------------


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


@pytest.mark.req(id="RQ-44")
def test_a_report_whose_metadata_is_entirely_garbage_still_records_the_run(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """RQ-44: the run row is what makes an abandoned session observable at
    all, so a malformed declared document MUST NOT block it (design.md
    D97's governing rule). *(Scenario: A malformed document does not block
    the run from being stored, `session-ingestion`)*."""
    run_id = "6" + "0" * 31
    report = _well_formed_report(run_id)
    report["metadata"] = _metadata_section(
        _metadata_file(content="not json at all", keys=["anything"])
    )

    response = client.post("/api/v1/runs", json=report)

    assert response.status_code == 201
    assert store.get_execution(run_id) is not None
    assert _stored_metadata_files(store, run_id) == frozenset(
        {MetadataFile(source_file="config/firmware.json", content_type="json", status="malformed")}
    )
    assert _stored_metadata_entries(store, run_id) == frozenset(
        {
            MetadataEntry(
                key="anything",
                value=None,
                source_file="config/firmware.json",
                status="source_unavailable",
            )
        }
    )


_VALUE_TOO_LARGE = "x" * (MAX_METADATA_VALUE_BYTES + 1)

# design.md D97's eleven classes, one case per row: (id, file, expected
# file row or None, expected entry rows). Class 11 (server-side shape
# reject) expects no rows at all -- the entry is dropped in its entirety.
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
def test_metadata_taxonomy_class_produces_the_exact_status_pair(
    client: TestClient,
    store: InMemoryExecutionStore,
    run_id: str,
    file_report: dict[str, Any],
    expected_file: MetadataFile | None,
    expected_entries: frozenset[MetadataEntry],
) -> None:
    """design.md D97's eleven classes, one test per row -- proves the exact
    `(file.status, key.status)` pair a client sees recorded, not merely that
    ingestion did not crash. *(Scenarios: "An unsupported format is treated
    as malformed" / "A malformed document does not block the run from being
    stored" / "A declared key absent from a well-formed document is marked
    absent" / "A non-scalar declared value is marked uncapturable, never
    serialized" / "An oversized value is dropped whole, marked
    uncapturable", `session-ingestion`)*."""
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
    """*(Scenario: A value within bound is stored whole, `session-ingestion`)*."""
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
    """*(Scenario: A report with no metadata section still records its run,
    `session-ingestion`)*."""
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
    """*(Scenario: A YAML declared document is parsed, `session-ingestion`)*."""
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
    """*(Scenario: An unsupported format is treated as malformed,
    `session-ingestion`)*. `toml` is a valid `run_metadata_file.content_type`
    (schema.sql's own `CHECK`) that `metadata_parse.parse` does not
    implement -- the same "server does not parse it" outcome D97 class 7
    covers for a genuinely broken document."""
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


# --- Phase 9: threat matrix (design.md, task 9.7) ---------------------------


def test_a_quoting_shaped_declared_key_round_trips_byte_identically(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Threat matrix: "Client-chosen text reaching SQL" -- bound parameters
    only, mirroring `test_routes_sections.py`'s own proof for section names.
    A declared key containing quote characters is stored and read back
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
    """Threat matrix: "Client-chosen text reaching a rejection body" -- an
    unknown field within the `metadata` section (`extra="forbid"`,
    design.md D96) is client-chosen text of the same shape a declared key
    could carry. `errors.py`'s pre-existing `safe_segment` allow-list, not
    new code in this phase, is what keeps it out of the response body."""
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
