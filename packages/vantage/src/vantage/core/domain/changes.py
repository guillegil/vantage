"""What changed in a run against its baseline: the one statement of which
earlier run a run is compared with, and of what each test's change is.

A run is compared once, by the store, in the transaction of the report that
gives it its exit status, and the comparison is never made again: the store
chooses the baseline by the rules below, restated in SQL from this module's
constants, then hands both runs' results to `compare` and stores the rows it
returns. Every run with an exit status has therefore been compared, and a
comparison once read never moves.

**The baseline** of a run R, within R's project, is the latest *complete*
run earlier than R -- earlier in the run list's own order, `(started_at,
id)`, so a tie on `started_at` is settled by id -- on R's branch when R has
one; failing that, or when R has no branch, the latest complete run earlier
than R on any branch, a run with none included. Two runs without a branch
never match as a branch: detached checkouts would otherwise compare with
each other. A run is complete when it ran to its end (`ran_to_its_end`)
with exit status 0 or 1: any other run -- stopped early, interrupted, one
that collected nothing, one pytest itself failed -- would make the next
run's tests read as new.

**A test's change** is `classify`'s, from its outcome in R and in the
baseline: failing means `failed` or `error`, pytest's own view, since a
non-strict xpass never fails a session and a strict one is reported as
`failed`. A test the baseline has and R lacks is `removed` when R ran to its
end, and `not_reached` otherwise, so an interrupted run's missing tests are
never called removed. A test that is still failing carries its streak: how
many consecutive runs failed it along R's chain of baselines, R included,
and the first of them.

Vocabularies are `frozenset`s of `str`, never enums, as everywhere in the
domain; `CHANGE_ORDER` is a tuple because its order is its meaning.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from vantage.core.domain.execution import Execution
from vantage.core.domain.result import OUTCOMES

FAILING_OUTCOMES = frozenset({"failed", "error"})
"""The outcomes that fail a test."""

RESULT_CHANGES = frozenset({"new_failure", "still_failing", "fixed", "new_test"})
"""The changes of a test the run holds a result for."""

MISSING_CHANGES = frozenset({"removed", "not_reached"})
"""The changes of a test the baseline has and the run lacks."""

CHANGES = RESULT_CHANGES | MISSING_CHANGES
"""Every change a comparison records; the schema's CHECK lists these."""

CHANGE_ORDER = ("new_failure", "still_failing", "fixed", "new_test", "removed", "not_reached")
"""The changes in the order a run's changes are read: what needs looking at
first, first."""

COMPARISON_STATES = frozenset({"pending", "none", "branch", "project"})
"""What `comparison_state` can say of a run: not compared yet, compared with
nothing, with a run of its own branch, or with the project's latest complete
run."""

BASELINE_EXIT_STATUSES = frozenset({0, 1})
"""The exit statuses of a run that can be a baseline: all tests passed, or
some failed."""

ENDED_EXIT_STATUSES = frozenset({0, 1, 5})
"""The exit statuses of a run that ran to its end: pytest's two verdicts,
and 5, a session that collected no test at all."""


@dataclass(frozen=True, slots=True)
class Streak:
    """How many consecutive runs failed a still-failing test along its run's
    chain of baselines, the run itself included, and the first of them."""

    runs: int
    since: str

    def __post_init__(self) -> None:
        if self.runs < 2:
            raise ValueError(f"a streak spans at least two runs, got {self.runs}")


@dataclass(frozen=True, slots=True)
class ChangeRow:
    """One test's change in a run's comparison, as a store writes it.

    `ordinal` is the result's index in the run's stored order, or, for a
    missing test, the test's index in the baseline's. `result_id` is the
    run's result, `None` for a missing test. `was` is the baseline's outcome,
    `None` when the baseline lacks the test. `streak` is set exactly for a
    still-failing test."""

    test_case_id: int
    change: str
    ordinal: int
    result_id: int | None
    was: str | None
    streak: Streak | None

    def __post_init__(self) -> None:
        if self.change not in CHANGES:
            raise ValueError(f"change must be one of {sorted(CHANGES)}, got {self.change!r}")
        if (self.result_id is None) != (self.change in MISSING_CHANGES):
            raise ValueError("result_id must be set exactly when the run holds the test")
        if (self.streak is None) != (self.change != "still_failing"):
            raise ValueError("streak must be set exactly for a still-failing test")
        if self.was is not None and self.was not in OUTCOMES:
            raise ValueError(f"was must be one of {sorted(OUTCOMES)}, got {self.was!r}")
        if (self.was is None) and self.change not in ("new_failure", "new_test"):
            raise ValueError("only a new test or a new failure may lack a baseline outcome")
        if self.was is not None and self.change == "new_test":
            raise ValueError("a new test has no baseline outcome")


def ran_to_its_end(execution: Execution) -> bool:
    """Whether the session ran every test it collected: it finished, was
    neither interrupted nor stopped early (`-x`, `--maxfail`, `--stepwise`, a
    collection error), and pytest gave it a verdict or found nothing to
    run."""
    return (
        execution.finished_at is not None
        and not execution.interrupted
        and execution.interrupt_reason is None
        and execution.exit_status in ENDED_EXIT_STATUSES
    )


def is_baseline_candidate(execution: Execution) -> bool:
    """Whether a run is complete, the only kind a later run is compared
    with: it ran to its end with a verdict on its tests."""
    return ran_to_its_end(execution) and execution.exit_status in BASELINE_EXIT_STATUSES


def classify(outcome: str, was: str | None) -> str | None:
    """A test's change from its outcome in the run and in the baseline,
    `was` being `None` when the baseline lacks it; `None` when nothing
    changed."""
    failing = outcome in FAILING_OUTCOMES
    if was is None:
        return "new_failure" if failing else "new_test"
    if was in FAILING_OUTCOMES:
        return "still_failing" if failing else "fixed"
    return "new_failure" if failing else None


def comparison_state(
    *,
    exit_status: int | None,
    branch: str | None,
    baseline_id: str | None,
    baseline_branch: str | None,
) -> str:
    """What a run was compared with, from its exit status and branch and its
    baseline's id and branch. Exact, never stored: the branch is always
    tried first, and neither run changes once it has an exit status."""
    if exit_status is None:
        return "pending"
    if baseline_id is None:
        return "none"
    if branch is not None and branch == baseline_branch:
        return "branch"
    return "project"


def compare(
    results: Iterable[tuple[int, int, str]],
    baseline_results: Iterable[tuple[int, str]],
    baseline_streaks: Mapping[int, Streak],
    *,
    baseline_id: str,
    ran_to_its_end: bool,
) -> list[ChangeRow]:
    """The rows a run's comparison stores: its own results that changed, in
    its stored order, then the baseline's tests it lacks, in the baseline's.

    `results` are the run's `(result_id, test_case_id, outcome)` in stored
    order, `baseline_results` the baseline's `(test_case_id, outcome)` in
    its stored order, and `baseline_streaks` the baseline's own still-failing
    streaks by test case. A still-failing test extends the baseline's streak
    for it, or starts one at the baseline when the baseline's row says the
    test newly failed there, or the baseline has no row for it because it
    was compared with nothing."""
    # A run holds one result per test, so each test case appears once, and
    # the dict keeps the baseline's stored order.
    was_by_case = dict(baseline_results)
    rows: list[ChangeRow] = []
    held: set[int] = set()
    for ordinal, (result_id, test_case_id, outcome) in enumerate(results):
        held.add(test_case_id)
        was = was_by_case.get(test_case_id)
        change = classify(outcome, was)
        if change is None:
            continue
        streak = None
        if change == "still_failing":
            prior = baseline_streaks.get(test_case_id)
            streak = (
                Streak(runs=2, since=baseline_id)
                if prior is None
                else Streak(runs=prior.runs + 1, since=prior.since)
            )
        rows.append(
            ChangeRow(
                test_case_id=test_case_id,
                change=change,
                ordinal=ordinal,
                result_id=result_id,
                was=was,
                streak=streak,
            )
        )
    missing = "removed" if ran_to_its_end else "not_reached"
    rows.extend(
        ChangeRow(
            test_case_id=test_case_id,
            change=missing,
            ordinal=ordinal,
            result_id=None,
            was=was,
            streak=None,
        )
        for ordinal, (test_case_id, was) in enumerate(was_by_case.items())
        if test_case_id not in held
    )
    return rows


def change_rank(change: str) -> int:
    """`change`'s place in `CHANGE_ORDER`."""
    return CHANGE_ORDER.index(change)


def wanted_changes(changes: Collection[str] | None) -> tuple[str, ...]:
    """The changes a filter keeps, in `CHANGE_ORDER`: every one without a
    filter, and of a filter only the words that are changes, so a word
    outside the vocabulary matches nothing."""
    if changes is None:
        return CHANGE_ORDER
    return tuple(change for change in CHANGE_ORDER if change in changes)


__all__ = [
    "BASELINE_EXIT_STATUSES",
    "CHANGES",
    "CHANGE_ORDER",
    "COMPARISON_STATES",
    "ENDED_EXIT_STATUSES",
    "FAILING_OUTCOMES",
    "MISSING_CHANGES",
    "RESULT_CHANGES",
    "ChangeRow",
    "Streak",
    "change_rank",
    "classify",
    "compare",
    "comparison_state",
    "is_baseline_candidate",
    "ran_to_its_end",
    "wanted_changes",
]
