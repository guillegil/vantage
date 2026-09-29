"""The comparison rules in `vantage.core.domain.changes`: which runs can be a
baseline, each test's change, the state a comparison reads as, and the rows
`compare` hands a store to write."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from vantage.core.domain.changes import (
    CHANGE_ORDER,
    CHANGES,
    FAILING_OUTCOMES,
    MISSING_CHANGES,
    RESULT_CHANGES,
    ChangeRow,
    Streak,
    classify,
    compare,
    comparison_state,
    is_baseline_candidate,
    ran_to_its_end,
    wanted_changes,
)
from vantage.core.domain.execution import Execution, Identity
from vantage.core.domain.result import OUTCOMES

_STARTED = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)
_BASELINE = "b" * 32
_EARLIER = "a" * 32


def _ended(
    exit_status: int | None,
    *,
    finished: bool = True,
    interrupted: bool = False,
    interrupt_reason: str | None = None,
) -> Execution:
    return Execution(
        identity=Identity("c" * 32),
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(seconds=3) if finished else None,
        exit_status=exit_status,
        interrupted=interrupted,
        interrupt_reason=interrupt_reason,
    )


# Every outcome in the run, against every outcome in the baseline or none.
_EXPECTED_CHANGE: dict[tuple[str, str | None], str | None] = {
    **{
        (outcome, was): (
            ("still_failing" if was in FAILING_OUTCOMES else "new_failure")
            if outcome in FAILING_OUTCOMES
            else ("fixed" if was in FAILING_OUTCOMES else None)
        )
        for outcome in OUTCOMES
        for was in OUTCOMES
    },
    **{
        (outcome, None): "new_failure" if outcome in FAILING_OUTCOMES else "new_test"
        for outcome in OUTCOMES
    },
}


@pytest.mark.parametrize(("outcome", "was"), sorted(_EXPECTED_CHANGE, key=str))
def test_classify_covers_every_outcome_against_every_baseline_outcome_or_none(
    outcome: str, was: str | None
) -> None:
    assert classify(outcome, was) == _EXPECTED_CHANGE[(outcome, was)]


def test_the_table_is_the_forty_two_cases_and_xpassed_is_not_failing() -> None:
    assert len(_EXPECTED_CHANGE) == 42
    assert FAILING_OUTCOMES == frozenset({"failed", "error"})
    # A non-strict xpass never fails a session: after a failure it is fixed,
    # and a new one is a new test rather than a new failure.
    assert classify("xpassed", "failed") == "fixed"
    assert classify("xpassed", None) == "new_test"
    assert classify("failed", "xpassed") == "new_failure"


def test_the_vocabularies_partition_the_changes_in_queue_order() -> None:
    assert CHANGES == RESULT_CHANGES | MISSING_CHANGES
    assert not RESULT_CHANGES & MISSING_CHANGES
    assert frozenset(CHANGE_ORDER) == CHANGES
    assert len(CHANGE_ORDER) == len(CHANGES)
    assert CHANGE_ORDER[:2] == ("new_failure", "still_failing")


@pytest.mark.parametrize(
    ("execution", "ended", "candidate"),
    [
        (_ended(0), True, True),
        (_ended(1), True, True),
        (_ended(2), False, False),
        (_ended(3), False, False),
        (_ended(4), False, False),
        (_ended(5), True, False),
        (_ended(7), False, False),
        (_ended(None, finished=False), False, False),
        (_ended(0, finished=False), False, False),
        (_ended(2, finished=False, interrupted=True), False, False),
        (_ended(0, interrupted=True), False, False),
        (_ended(1, interrupt_reason="stopping after 1 failures"), False, False),
    ],
    ids=[
        "exit-0",
        "exit-1",
        "exit-2",
        "exit-3",
        "exit-4",
        "exit-5",
        "exit-7",
        "running",
        "no-finish-time",
        "ctrl-c",
        "interrupted-flag",
        "stopped-early",
    ],
)
def test_a_run_ran_to_its_end_and_is_complete_only_as_the_rules_say(
    execution: Execution, ended: bool, candidate: bool
) -> None:
    assert ran_to_its_end(execution) is ended
    assert is_baseline_candidate(execution) is candidate


@pytest.mark.parametrize(
    ("exit_status", "branch", "baseline_id", "baseline_branch", "state"),
    [
        (None, "main", None, None, "pending"),
        (0, "main", None, None, "none"),
        (1, None, None, None, "none"),
        (0, "main", _BASELINE, "main", "branch"),
        (0, "feat/x", _BASELINE, "main", "project"),
        (0, None, _BASELINE, "main", "project"),
        (0, "main", _BASELINE, None, "project"),
        # Two runs without a branch are never on one branch.
        (0, None, _BASELINE, None, "project"),
    ],
)
def test_comparison_state_names_what_the_run_was_compared_with(
    exit_status: int | None,
    branch: str | None,
    baseline_id: str | None,
    baseline_branch: str | None,
    state: str,
) -> None:
    assert (
        comparison_state(
            exit_status=exit_status,
            branch=branch,
            baseline_id=baseline_id,
            baseline_branch=baseline_branch,
        )
        == state
    )


def test_compare_keeps_only_changed_tests_at_their_ordinals() -> None:
    rows = compare(
        [
            (101, 1, "passed"),  # unchanged
            (102, 2, "failed"),  # new failure
            (103, 3, "passed"),  # fixed
            (104, 4, "skipped"),  # new test
            (105, 5, "error"),  # new test whose first run failed
            (106, 6, "xfailed"),  # unchanged
        ],
        [(1, "passed"), (2, "passed"), (3, "error"), (6, "xfailed")],
        {},
        baseline_id=_BASELINE,
        ran_to_its_end=True,
    )

    assert rows == [
        ChangeRow(2, "new_failure", 1, 102, "passed", None),
        ChangeRow(3, "fixed", 2, 103, "error", None),
        ChangeRow(4, "new_test", 3, 104, None, None),
        ChangeRow(5, "new_failure", 4, 105, None, None),
    ]


@pytest.mark.parametrize(("ended", "missing"), [(True, "removed"), (False, "not_reached")])
def test_the_tests_a_run_lacks_follow_in_the_baselines_order_all_of_one_kind(
    ended: bool, missing: str
) -> None:
    rows = compare(
        [(201, 2, "passed")],
        [(3, "failed"), (2, "passed"), (1, "skipped"), (4, "passed")],
        {},
        baseline_id=_BASELINE,
        ran_to_its_end=ended,
    )

    assert rows == [
        ChangeRow(3, missing, 0, None, "failed", None),
        ChangeRow(1, missing, 2, None, "skipped", None),
        ChangeRow(4, missing, 3, None, "passed", None),
    ]


def test_a_still_failing_streak_extends_the_baselines_or_starts_at_the_baseline() -> None:
    rows = compare(
        [(301, 1, "failed"), (302, 2, "error"), (303, 3, "failed")],
        [(1, "failed"), (2, "failed"), (3, "error")],
        # Test 1 was already still failing in the baseline, since `_EARLIER`;
        # test 2 newly failed there, so the baseline has no streak for it;
        # test 3's baseline was compared with nothing, so it has none either.
        {1: Streak(runs=3, since=_EARLIER)},
        baseline_id=_BASELINE,
        ran_to_its_end=True,
    )

    assert [(row.test_case_id, row.change, row.streak) for row in rows] == [
        (1, "still_failing", Streak(runs=4, since=_EARLIER)),
        (2, "still_failing", Streak(runs=2, since=_BASELINE)),
        (3, "still_failing", Streak(runs=2, since=_BASELINE)),
    ]
    assert [row.was for row in rows] == ["failed", "failed", "error"]


def test_a_streak_is_ignored_for_a_test_no_longer_failing() -> None:
    rows = compare(
        [(401, 1, "passed")],
        [(1, "failed")],
        {1: Streak(runs=5, since=_EARLIER)},
        baseline_id=_BASELINE,
        ran_to_its_end=True,
    )

    assert rows == [ChangeRow(1, "fixed", 0, 401, "failed", None)]


def test_nothing_changed_is_no_rows() -> None:
    """Outcomes may differ without a change: skipped and xfailed are both
    not failing."""
    rows = compare(
        [(1, 1, "passed"), (2, 2, "skipped")],
        [(1, "passed"), (2, "xfailed")],
        {},
        baseline_id=_BASELINE,
        ran_to_its_end=False,
    )

    assert rows == []


@pytest.mark.parametrize(
    "fields",
    [
        {"change": "renamed"},
        {"change": "removed"},
        {"result_id": None},
        {"streak": Streak(2, _BASELINE)},
        {"change": "still_failing"},
        {"was": "gone"},
        {"change": "fixed", "was": None},
        {"change": "new_test", "was": "passed"},
    ],
    ids=[
        "unknown-change",
        "missing-with-result",
        "result-change-without-result",
        "streak-not-still-failing",
        "still-failing-without-streak",
        "unknown-was",
        "fixed-without-was",
        "new-test-with-was",
    ],
)
def test_a_row_no_schema_would_store_is_refused(fields: dict[str, object]) -> None:
    base: dict[str, object] = {
        "test_case_id": 1,
        "change": "new_failure",
        "ordinal": 0,
        "result_id": 7,
        "was": "passed",
        "streak": None,
    }
    with pytest.raises(ValueError):
        ChangeRow(**{**base, **fields})  # type: ignore[arg-type]


def test_a_streak_spans_at_least_two_runs() -> None:
    with pytest.raises(ValueError, match="at least two"):
        Streak(runs=1, since=_BASELINE)


def test_a_filter_keeps_only_changes_in_queue_order() -> None:
    assert wanted_changes(None) == CHANGE_ORDER
    assert wanted_changes(["fixed", "new_failure", "sideways"]) == ("new_failure", "fixed")
    assert wanted_changes([]) == ()
