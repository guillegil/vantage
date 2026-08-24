"""`Result`, `CaseIdentity` and `CatalogueEntry`'s frozen, nullable shape
(design.md, Interfaces / Contracts and D17-D18).

Mirrors `test_execution.py`. The outcome vocabulary, the `""`-vs-`None`
distinction on `param_id`, and the "a phase that never ran is NULL, never
zero" rule are all proved at this layer because a design that gets any of
them wrong at the dataclass hop gets them wrong everywhere downstream.
"""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from vantage.core.domain.result import (
    OUTCOMES,
    CapturedOutput,
    CaseIdentity,
    CatalogueEntry,
    FailureEvidence,
    Result,
)

_IDENTITY = CaseIdentity(
    node_id="packages/vantage/tests/test_result.py::test_example",
    file_path="packages/vantage/tests/test_result.py",
    class_name=None,
    function_name="test_example",
    param_id=None,
)


def _result(**overrides: object) -> Result:
    fields: dict[str, object] = {
        "identity": _IDENTITY,
        "outcome": "passed",
        "duration": 0.1,
        "started_at": datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 8, 18, 9, 0, 1, tzinfo=timezone.utc),
        "setup_outcome": "passed",
        "call_outcome": "passed",
        "teardown_outcome": "passed",
        "setup_duration": 0.01,
        "call_duration": 0.08,
        "teardown_duration": 0.01,
        "worker_id": None,
    }
    fields.update(overrides)
    return Result(**fields)  # type: ignore[arg-type]


# --- 1.2: outcome vocabulary -------------------------------------------------


@pytest.mark.parametrize("outcome", sorted(OUTCOMES))
def test_result_accepts_every_outcome_in_the_check_constraint_vocabulary(outcome: str) -> None:
    result = _result(outcome=outcome)

    assert result.outcome == outcome


def test_outcomes_has_exactly_the_six_values_the_result_outcome_check_declares() -> None:
    assert OUTCOMES == frozenset({"passed", "failed", "error", "skipped", "xfailed", "xpassed"})


def test_result_rejects_an_outcome_outside_outcomes() -> None:
    with pytest.raises(ValueError, match="outcome"):
        _result(outcome="not-a-real-outcome")


# --- 1.3: ""-vs-None on param_id, the dataclass hop --------------------------


def test_case_identity_empty_string_param_id_is_not_equal_to_none_param_id() -> None:
    empty = CaseIdentity(
        node_id="packages/vantage/tests/test_x.py::test_x[]",
        file_path="packages/vantage/tests/test_x.py",
        class_name=None,
        function_name="test_x",
        param_id="",
    )
    absent = CaseIdentity(
        node_id="packages/vantage/tests/test_x.py::test_x",
        file_path="packages/vantage/tests/test_x.py",
        class_name=None,
        function_name="test_x",
        param_id=None,
    )

    assert empty != absent
    assert empty.param_id == ""
    assert absent.param_id is None


def test_case_identity_empty_string_param_id_survives_construction_unchanged() -> None:
    identity = CaseIdentity(
        node_id="packages/vantage/tests/test_x.py::test_x[]",
        file_path="packages/vantage/tests/test_x.py",
        class_name=None,
        function_name="test_x",
        param_id="",
    )

    assert identity.param_id == ""
    assert identity.param_id is not None


# --- 1.4: null class_name and a genuine 0.0 phase duration -------------------


def test_case_identity_class_name_is_none_for_a_module_level_test() -> None:
    identity = CaseIdentity(
        node_id="packages/vantage/tests/test_x.py::test_module_level",
        file_path="packages/vantage/tests/test_x.py",
        class_name=None,
        function_name="test_module_level",
        param_id=None,
    )

    assert identity.class_name is None


def test_case_identity_class_name_is_set_for_a_class_scoped_test() -> None:
    identity = CaseIdentity(
        node_id="packages/vantage/tests/test_x.py::TestX::test_method",
        file_path="packages/vantage/tests/test_x.py",
        class_name="TestX",
        function_name="test_method",
        param_id=None,
    )

    assert identity.class_name == "TestX"


def test_result_phase_duration_of_zero_survives_as_zero_not_none() -> None:
    result = _result(setup_duration=0.0)

    assert result.setup_duration == 0.0
    assert result.setup_duration is not None


def test_result_phase_duration_that_never_ran_is_none_not_zero() -> None:
    result = _result(call_duration=None)

    assert result.call_duration is None


# --- structural: frozen, matches Execution's shape ---------------------------


def test_result_is_frozen() -> None:
    result = _result()

    with pytest.raises(FrozenInstanceError):
        result.outcome = "failed"  # type: ignore[misc]


# --- 1.6: Result gains failure and captured fields (design.md D77) ---------


def test_result_gains_failure_and_captured_fields() -> None:
    """design.md D77: `Result` carries `failure: FailureEvidence | None` and
    `captured: CapturedOutput`; `FailureEvidence` carries its 13 named
    fields, `CapturedOutput` its 4."""
    result_field_names = {f.name for f in dataclasses.fields(Result)}
    assert "failure" in result_field_names
    assert "captured" in result_field_names

    failure_field_names = {f.name for f in dataclasses.fields(FailureEvidence)}
    assert failure_field_names == {
        "failure_type",
        "failure_message",
        "failure_message_truncated",
        "failure_path",
        "failure_lineno",
        "failure_repr",
        "failure_repr_truncated",
        "traceback",
        "traceback_truncated",
        "skip_reason",
        "skip_reason_truncated",
        "xfail_reason",
        "xfail_reason_truncated",
    }

    captured_field_names = {f.name for f in dataclasses.fields(CapturedOutput)}
    assert captured_field_names == {"stdout", "stdout_truncated", "stderr", "stderr_truncated"}

    failure = FailureEvidence(
        failure_type="AssertionError",
        failure_message="AssertionError: assert 1200 == 1320",
        failure_message_truncated=False,
        failure_path="tests/helpers/pricing.py",
        failure_lineno=47,
        failure_repr="AssertionError('assert 1200 == 1320')",
        failure_repr_truncated=False,
        traceback="tests/test_orders.py:19: in test_total_includes_tax\n    ...",
        traceback_truncated=False,
        skip_reason=None,
        skip_reason_truncated=False,
        xfail_reason=None,
        xfail_reason_truncated=False,
    )
    captured = CapturedOutput(
        stdout="", stdout_truncated=False, stderr=None, stderr_truncated=False
    )
    result = _result(failure=failure, captured=captured)

    assert result.failure is failure
    assert result.captured is captured


def test_catalogue_entry_holds_identity_and_the_two_timestamps() -> None:
    entry = CatalogueEntry(
        identity=_IDENTITY,
        first_seen_at=datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc),
        last_seen_run_id="a" * 32,
    )

    assert entry.identity == _IDENTITY
    assert entry.first_seen_at < entry.last_seen_at
    assert entry.last_seen_run_id == "a" * 32
