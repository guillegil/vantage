"""`Result` accepts exactly the outcome vocabulary `result.outcome`'s CHECK
constraint declares, and refuses anything else at construction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from vantage.core.domain.result import OUTCOMES, CaseIdentity, Result

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


@pytest.mark.parametrize("outcome", sorted(OUTCOMES))
def test_result_accepts_every_outcome_in_the_check_constraint_vocabulary(outcome: str) -> None:
    result = _result(outcome=outcome)

    assert result.outcome == outcome


def test_result_rejects_an_outcome_outside_outcomes() -> None:
    with pytest.raises(ValueError, match="outcome"):
        _result(outcome="not-a-real-outcome")
