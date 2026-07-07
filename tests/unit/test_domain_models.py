"""RED: Run/TestResult/Phase/Artifact are immutable, and RunState is closed
over the six lifecycle states (spec Domain 2 -- Run Lifecycle)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from vantage.core.domain.models import Artifact, Phase, Run, RunState, TestResult


def test_run_state_has_exactly_six_members() -> None:
    assert {member.value for member in RunState} == {
        "pending",
        "running",
        "finished",
        "stopped",
        "failed",
        "failed-to-start",
    }


def _make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {
        "run_id": "01J000000000000000000000",
        "state": RunState.PENDING,
        "testpath": "tests/",
        "user": "alice",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Run(**defaults)  # type: ignore[arg-type]


def test_run_rejects_mutation() -> None:
    run = _make_run()
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.state = RunState.RUNNING  # type: ignore[misc]


def test_run_defaults_are_unset() -> None:
    run = _make_run()
    assert run.started_at is None
    assert run.finished_at is None
    assert run.exit_code is None
    assert run.totals == {}


def test_run_accepts_enriched_selection_metadata() -> None:
    run = _make_run(
        seed=42,
        seed_source="pytest-strategies",
        marker_expr="slow",
        keyword_expr="test_foo",
    )
    assert run.seed == 42
    assert run.seed_source == "pytest-strategies"
    assert run.marker_expr == "slow"
    assert run.keyword_expr == "test_foo"


def test_run_defaults_enriched_selection_metadata_to_none() -> None:
    run = _make_run()
    assert run.seed is None
    assert run.seed_source is None
    assert run.marker_expr is None
    assert run.keyword_expr is None


def _make_phase(**overrides: object) -> Phase:
    defaults: dict[str, object] = {"name": "call", "outcome": "passed", "duration": 0.01}
    defaults.update(overrides)
    return Phase(**defaults)  # type: ignore[arg-type]


def test_phase_rejects_mutation() -> None:
    phase = _make_phase()
    with pytest.raises(dataclasses.FrozenInstanceError):
        phase.outcome = "failed"  # type: ignore[misc]


def test_phase_defaults_captures_to_none() -> None:
    phase = _make_phase()
    assert phase.captures is None


def test_phase_accepts_captures() -> None:
    phase = _make_phase(
        captures={"stdout": "hi", "stderr": "", "log": "", "truncated": False}
    )
    assert phase.captures == {"stdout": "hi", "stderr": "", "log": "", "truncated": False}


def _make_test_result(**overrides: object) -> TestResult:
    defaults: dict[str, object] = {
        "id": None,
        "run_id": "01J000000000000000000000",
        "node_id": "tests/test_x.py::test_y",
        "outcome": "passed",
        "duration": 0.02,
        "phases": {"call": _make_phase()},
    }
    defaults.update(overrides)
    return TestResult(**defaults)  # type: ignore[arg-type]


def test_test_result_rejects_mutation() -> None:
    result = _make_test_result()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.outcome = "failed"  # type: ignore[misc]


def test_test_result_records_producer_reported_outcome_verbatim() -> None:
    result = _make_test_result(outcome="xfail")
    assert result.outcome == "xfail"


def test_test_result_defaults_enriched_identity_fields_to_empty() -> None:
    result = _make_test_result()
    assert result.base_test_id is None
    assert result.relpath is None
    assert result.lineno is None
    assert result.originalname is None
    assert result.parameters == {}
    assert result.fixture_names == ()


def test_test_result_accepts_enriched_identity_and_parameters() -> None:
    result = _make_test_result(
        base_test_id="abc123",
        relpath="tests/test_x.py",
        lineno=10,
        originalname="test_y",
        parameters={"a": {"value_repr": "1", "type": "int"}},
        fixture_names=("tmp_path", "monkeypatch"),
    )
    assert result.base_test_id == "abc123"
    assert result.relpath == "tests/test_x.py"
    assert result.lineno == 10
    assert result.originalname == "test_y"
    assert result.parameters == {"a": {"value_repr": "1", "type": "int"}}
    assert result.fixture_names == ("tmp_path", "monkeypatch")


def _make_artifact(**overrides: object) -> Artifact:
    defaults: dict[str, object] = {
        "id": None,
        "run_id": "01J000000000000000000000",
        "kind": "log",
        "path": "artifacts/01J000000000000000000000/session.log",
        "size": 128,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Artifact(**defaults)  # type: ignore[arg-type]


def test_artifact_rejects_mutation() -> None:
    artifact = _make_artifact()
    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.path = "elsewhere"  # type: ignore[misc]


def test_artifact_stores_path_reference_not_content() -> None:
    artifact = _make_artifact()
    assert isinstance(artifact.path, str)
    assert not hasattr(artifact, "content")
