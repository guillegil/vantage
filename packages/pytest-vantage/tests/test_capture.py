"""Table-driven tests for `pytest_vantage.capture`: identity decomposition,
phase accumulation, outcome derivation and result payload assembly. Pure
functions over data, no subprocess or server needed; real `pytest.TestReport`
instances stand in for what `Recorder` receives at runtime.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal

import pytest
from pytest_vantage import vcs
from pytest_vantage.capture import (
    PendingResult,
    _select_evidence_phase,
    accumulate,
    assemble_results,
    build_result,
    decompose,
    derive_outcome,
    isoformat_utc,
)

_Phase = Literal["setup", "call", "teardown"]
_ReportOutcome = Literal["passed", "failed", "skipped"]


def _report(
    when: _Phase,
    outcome: _ReportOutcome,
    *,
    wasxfail: str | None = None,
    duration: float = 0.0,
    start: float = 0.0,
    stop: float = 0.0,
    nodeid: str = "test_nid.py::test_it",
) -> pytest.TestReport:
    """A real `pytest.TestReport`, the exact type `Recorder` passes at
    runtime. `wasxfail` is set only when given, matching pytest's own
    dynamic-attribute behaviour: present or absent, never `None` --
    `derive_outcome`'s `hasattr` check depends on this.
    """
    report = pytest.TestReport(
        nodeid=nodeid,
        location=(nodeid.split("::", 1)[0], 0, nodeid),
        keywords={},
        outcome=outcome,
        longrepr=None,
        when=when,
        duration=duration,
        start=start,
        stop=stop,
    )
    if wasxfail is not None:
        report.wasxfail = wasxfail
    return report


def _pending(*reports: pytest.TestReport) -> PendingResult:
    """The accumulated state for one node id, built the way `Recorder` builds
    it: every report goes through `accumulate`, in the order given."""
    pending: dict[str, PendingResult] = {}
    for report in reports:
        accumulate(pending, report)
    (entry,) = pending.values()
    return entry


@pytest.mark.parametrize(
    (
        "node_id",
        "expected_file_path",
        "expected_class_name",
        "expected_function_name",
        "expected_param_id",
    ),
    [
        pytest.param(
            "packages/vantage/tests/test_execution.py::test_it_passes",
            "packages/vantage/tests/test_execution.py",
            None,
            "test_it_passes",
            None,
            id="module-level-unparametrised",
        ),
        pytest.param(
            "packages/vantage/tests/test_memory_store.py"
            "::TestInMemoryExecutionStore::test_first_write_creates_a_row",
            "packages/vantage/tests/test_memory_store.py",
            "TestInMemoryExecutionStore",
            "test_first_write_creates_a_row",
            None,
            id="single-class",
        ),
        pytest.param(
            "packages/vantage/tests/test_x.py::Outer::Inner::test_nested",
            "packages/vantage/tests/test_x.py",
            "Outer::Inner",
            "test_nested",
            None,
            id="nested-class-segments-joined-with-double-colon",
        ),
        pytest.param(
            "test_nid.py::test_p[a::b]",
            "test_nid.py",
            None,
            "test_p",
            "a::b",
            id="parametrised-value-itself-contains-double-colon",
        ),
        pytest.param(
            "test_nid.py::TestOuter::TestInner::test_q[m::n]",
            "test_nid.py",
            "TestOuter::TestInner",
            "test_q",
            "m::n",
            id="nested-class-and-a-parametrised-value-containing-double-colon",
        ),
    ],
)
def test_decompose_identity_class_name_and_unparametrised_param_id(
    node_id: str,
    expected_file_path: str,
    expected_class_name: str | None,
    expected_function_name: str,
    expected_param_id: str | None,
) -> None:
    """A module-level test's `class_name` is `None`, never `""`. A nested
    class's segments join with `"::"`. An unparametrised test's `param_id`
    is `None`.
    """
    identity = decompose(node_id)

    assert identity.file_path == expected_file_path
    assert identity.class_name == expected_class_name
    assert identity.function_name == expected_function_name
    assert identity.param_id == expected_param_id
    if expected_param_id is None:
        assert identity.param_id is None  # None must stay None, never a computed ""


def test_decompose_identity_empty_brackets_is_empty_string_not_none() -> None:
    """The brackets are the evidence of parametrisation; their content is
    not. This is not a hypothetical case -- this exact node id exists in
    this repository today:
    `test_execution.py::test_identity_rejects_anything_but_32_lowercase_hex_characters[]`.
    It IS parametrised and its parameter id is the empty string, which must
    survive as `""`, not be coerced to `None` (the forbidden idiom `x or
    None`).
    """
    node_id = (
        "packages/vantage/tests/test_execution.py"
        "::test_identity_rejects_anything_but_32_lowercase_hex_characters[]"
    )

    identity = decompose(node_id)

    assert identity.function_name == (
        "test_identity_rejects_anything_but_32_lowercase_hex_characters"
    )
    assert identity.param_id == ""
    assert identity.param_id is not None


def test_decompose_identity_slices_on_first_and_last_bracket_not_partition_symmetry() -> None:
    """A parameter id that itself contains brackets must survive intact.
    Slicing on the first `"["` and the last `"]"` gets this right; a
    symmetric `partition`/`rpartition` split would not.
    """
    node_id = "packages/vantage/tests/test_x.py::test_x[[0]]"

    identity = decompose(node_id)

    assert identity.function_name == "test_x"
    assert identity.param_id == "[0]"


def test_decompose_identity_directory_containing_brackets_is_not_mistaken_for_a_parameter() -> None:
    """A directory component may itself contain brackets. The parameter
    section must be located within the remainder AFTER the file path has
    already been split off -- searching the whole `node_id` for a bracket
    would misidentify this directory as the start of a parameter section
    and corrupt every field that follows it.
    """
    node_id = "tests/data[1]/test_a.py::test_b[x]"

    identity = decompose(node_id)

    assert identity.file_path == "tests/data[1]/test_a.py"
    assert identity.class_name is None
    assert identity.function_name == "test_b"
    assert identity.param_id == "x"


@pytest.mark.parametrize(
    ("node_id", "expected_function_name"),
    [
        pytest.param("style_z.txt", "style_z.txt", id="at-the-rootdir"),
        pytest.param("lint/style_z.txt", "style_z.txt", id="in-a-directory"),
    ],
)
def test_decompose_identity_of_an_item_that_is_its_own_file(
    node_id: str, expected_function_name: str
) -> None:
    """An item that is also a `pytest.File` (the pattern older lint plugins
    use; pytest warns but runs it) has the bare file path as its node id,
    with no `"::"`. It is still a test pytest ran, so it decomposes rather
    than raising: the whole id is the file, and the file's name is the
    function name.
    """
    identity = decompose(node_id)

    assert identity.node_id == node_id
    assert identity.file_path == node_id
    assert identity.class_name is None
    assert identity.function_name == expected_function_name
    assert identity.param_id is None


# --- outcome derivation: the nine precedence rows ----------------------------

# (setup_outcome, call_spec, teardown_outcome, expected_outcome); call_spec is
# (call_outcome, wasxfail) or None when setup did not pass (call never runs).
_OutcomeRow = tuple[_ReportOutcome, "tuple[_ReportOutcome, str | None] | None", _ReportOutcome, str]
_OUTCOME_ROWS: list[_OutcomeRow] = [
    ("failed", None, "passed", "error"),  # row 1: setup failure
    ("skipped", None, "passed", "skipped"),  # row 2: skip mark
    ("passed", ("skipped", "expected"), "passed", "xfailed"),  # row 3: failing xfail
    ("passed", ("skipped", None), "passed", "skipped"),  # row 4
    ("passed", ("failed", "expected"), "passed", "xfailed"),  # row 5
    ("passed", ("failed", None), "passed", "failed"),  # row 6
    ("passed", ("passed", "expected"), "passed", "xpassed"),  # row 7: non-strict xpass
    ("passed", ("passed", None), "failed", "error"),  # row 8: teardown failure
    ("passed", ("passed", None), "passed", "passed"),  # row 9
]


@pytest.mark.parametrize(
    ("setup_outcome", "call_spec", "teardown_outcome", "expected_outcome"),
    _OUTCOME_ROWS,
    ids=[f"row-{n}" for n in range(1, 10)],
)
def test_derive_outcome_all_nine_precedence_rows(
    setup_outcome: _ReportOutcome,
    call_spec: tuple[_ReportOutcome, str | None] | None,
    teardown_outcome: _ReportOutcome,
    expected_outcome: str,
) -> None:
    """The nine-row outcome precedence table, evaluated in order."""
    setup = _report("setup", setup_outcome)
    call = _report("call", call_spec[0], wasxfail=call_spec[1]) if call_spec else None
    teardown = _report("teardown", teardown_outcome)

    assert derive_outcome(setup, call, teardown) == expected_outcome


# --- a strict xfail that passes is failed, not xpassed ----------------------


def test_derive_outcome_strict_xfail_that_passes_is_failed_not_xpassed() -> None:
    """pytest sets `outcome="failed"` with `wasxfail` entirely ABSENT for
    `[XPASS(strict)]` (see `_pytest/skipping.py`'s
    `pytest_runtest_makereport`), so only a non-strict xpass is `xpassed`.
    """
    setup = _report("setup", "passed")
    call = _report("call", "failed")  # no wasxfail -- this IS the strict-xpass shape
    teardown = _report("teardown", "passed")

    assert derive_outcome(setup, call, teardown) == "failed"


@pytest.mark.parametrize("reason", ["[NOTRUN] never run", ""])
def test_an_xfail_raised_during_setup_is_xfailed_with_its_evidence_from_setup(
    reason: str,
) -> None:
    """`xfail(run=False)` and `pytest.xfail()` in a fixture end the test in a
    skipped setup carrying `wasxfail`, so call never runs. That is
    `xfailed`, and its reason lives on the setup report."""
    setup = _report("setup", "skipped", wasxfail=reason)
    teardown = _report("teardown", "passed")

    outcome = derive_outcome(setup, None, teardown)

    assert outcome == "xfailed"
    assert _select_evidence_phase(setup, None, teardown, outcome) is setup


# --- teardown downgrades only a passed result --------------------------------

# Row 8 (in the table above) already proves the positive case -- `passed` IS
# downgraded. This proves the negative: every OTHER verdict keeps its own
# word despite the same failing teardown.
_KEEPS_OWN_WORD: list[tuple[_ReportOutcome, str | None, str]] = [
    ("failed", None, "failed"),
    ("skipped", "expected", "xfailed"),
    ("passed", "expected", "xpassed"),
    ("skipped", None, "skipped"),
]


@pytest.mark.parametrize(
    ("call_outcome", "call_wasxfail", "expected_outcome"),
    _KEEPS_OWN_WORD,
    ids=["failed", "xfailed", "xpassed", "skipped"],
)
def test_derive_outcome_teardown_failure_downgrades_only_a_passed_result(
    call_outcome: _ReportOutcome, call_wasxfail: str | None, expected_outcome: str
) -> None:
    """Row 8's downgrade to `error` applies ONLY to row 9's `passed`; every
    other verdict keeps its own word."""
    setup = _report("setup", "passed")
    call = _report("call", call_outcome, wasxfail=call_wasxfail)
    teardown = _report("teardown", "failed")

    assert derive_outcome(setup, call, teardown) == expected_outcome


# --- the JSON hop: null vs zero ---------------------------------------------


def test_build_result_phase_duration_null_vs_zero_survives_the_json_hop() -> None:
    """A phase that never ran serialises as JSON `null`, never `0.0`; a
    genuine `0.0` survives as `0.0` -- never `x or None` -- checked at the
    actual `json.dumps` boundary, not just the Python dict.
    """
    # No call report -- setup failed, so call never ran.
    pending = _pending(
        _report("setup", "failed", duration=0.0),  # ran, genuinely instant
        _report("teardown", "passed", duration=0.0031),
    )

    result = build_result("test_nid.py::test_it", pending)
    assert result is not None

    serialised = json.dumps(result)
    payload = json.loads(serialised)

    assert payload["setup_duration"] == 0.0  # genuine zero survives
    assert payload["call_duration"] is None  # call never ran -- null, not 0.0
    assert payload["call_outcome"] is None
    assert '"call_duration": null' in serialised
    assert '"setup_duration": 0.0' in serialised


# --- fixed-width ISO-8601 timestamps ---------------------------------------
#
# A variable-width timestamp breaks lexicographic ordering, and the server's
# parsing tolerates variable width, so no end-to-end test would notice.


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        pytest.param(
            datetime(2026, 8, 15, 9, 14, 2, 0, tzinfo=timezone.utc),
            "2026-08-15T09:14:02.000000+00:00",
            id="zero-microseconds",
        ),
        pytest.param(
            datetime(2026, 8, 15, 9, 14, 2, 481930, tzinfo=timezone.utc),
            "2026-08-15T09:14:02.481930+00:00",
            id="nonzero-microseconds",
        ),
        pytest.param(
            datetime(999, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc),
            "0999-01-02T03:04:05.000006+00:00",
            id="year-below-1000",
        ),
        pytest.param(
            datetime(2026, 8, 15, 11, 14, 2, 0, tzinfo=timezone(timedelta(hours=2))),
            "2026-08-15T09:14:02.000000+00:00",
            id="converted-to-utc",
        ),
    ],
)
def test_isoformat_utc_is_fixed_width_utc(moment: datetime, expected: str) -> None:
    formatted = isoformat_utc(moment)

    assert formatted == expected
    assert len(formatted) == len("YYYY-MM-DDTHH:MM:SS.ffffff+00:00")


# --- a result without a teardown report is dropped -------------------------


def test_build_result_returns_none_when_teardown_was_never_seen() -> None:
    """Setup+call with no teardown (e.g. interrupted mid-session) is
    DROPPED, not invented -- `assemble_results` relies on this `None` to skip
    the entry."""
    # No teardown report was ever seen.
    pending = _pending(_report("setup", "passed"), _report("call", "passed"))

    assert build_result("test_nid.py::test_it", pending) is None


# --- accumulation mechanics -------------------------------------------------


_ALL_PHASES_PASSING: tuple[tuple[_Phase, _ReportOutcome], ...] = (
    ("setup", "passed"),
    ("call", "passed"),
    ("teardown", "passed"),
)


def test_accumulate_overwrites_a_duplicate_report_for_the_same_phase() -> None:
    """A duplicate report for the same node id and phase overwrites, never a
    second row."""
    pending: dict[str, PendingResult] = {}
    for report in (
        _report("setup", "passed"),
        _report("call", "failed"),
        _report("call", "passed"),
        _report("teardown", "passed"),
    ):
        accumulate(pending, report)

    (result,) = assemble_results(pending)

    assert result["call_outcome"] == "passed"


def _on_worker(report: pytest.TestReport, worker_id: str) -> pytest.TestReport:
    """The report as the xdist controller receives it from `worker_id`."""
    report.worker_id = worker_id  # type: ignore[attr-defined]
    return report


def test_executions_on_different_workers_are_never_stitched_together() -> None:
    """Under `--dist each` every worker runs every test, and the controller
    receives their reports interleaved. Phases from different workers must
    not merge into one result; the most severe execution is kept whole,
    so a failure on one worker is never hidden by a pass on another."""
    pending: dict[str, PendingResult] = {}
    for report in (
        _on_worker(_report("setup", "passed", duration=0.5, start=20.0), "gw1"),
        _on_worker(_report("setup", "passed", duration=0.25, start=10.0), "gw0"),
        _on_worker(_report("call", "failed"), "gw0"),
        _on_worker(_report("call", "passed"), "gw1"),
        _on_worker(_report("teardown", "passed"), "gw0"),
        _on_worker(_report("teardown", "passed"), "gw1"),
    ):
        accumulate(pending, report)

    (result,) = assemble_results(pending)

    assert result["outcome"] == "failed"
    assert result["worker_id"] == "gw0"
    assert result["call_outcome"] == "failed"
    assert result["setup_duration"] == 0.25
    assert result["started_at"] == "1970-01-01T00:00:10.000000+00:00"


def test_executions_with_the_same_verdict_keep_the_first_one_seen() -> None:
    """Between executions no worse than each other, the choice is stable:
    the first worker to report the test."""
    pending: dict[str, PendingResult] = {}
    for worker_id in ("gw1", "gw0"):
        for when, outcome in _ALL_PHASES_PASSING:
            accumulate(pending, _on_worker(_report(when, outcome), worker_id))

    (result,) = assemble_results(pending)

    assert result["worker_id"] == "gw1"


def _crash(nodeid: str) -> pytest.TestReport:
    """The report xdist logs on the controller for a test whose worker died
    while running it: no phase, outcome failed, and the worker on `node`."""
    report = pytest.TestReport(
        nodeid=nodeid,
        location=(nodeid.split("::", 1)[0], None, nodeid.split("::", 1)[0]),
        keywords={},
        outcome="failed",
        longrepr=f"worker 'gw1' crashed while running {nodeid!r}",
        when="???",  # type: ignore[arg-type]  # exactly what xdist sends
    )
    report.node = SimpleNamespace(gateway=SimpleNamespace(id="gw1"))  # type: ignore[attr-defined]
    return report


@pytest.mark.parametrize(
    ("seen_before", "setup_outcome", "call_outcome"),
    [
        pytest.param((), None, None, id="before-setup"),
        pytest.param((("setup", "passed"),), "passed", None, id="during-call"),
        pytest.param((("setup", "passed"), ("call", "passed")), "passed", "passed", id="teardown"),
    ],
)
def test_a_test_whose_worker_crashed_is_recorded_as_failed(
    seen_before: tuple[tuple[_Phase, _ReportOutcome], ...],
    setup_outcome: str | None,
    call_outcome: str | None,
) -> None:
    """When an xdist worker dies mid-test, xdist logs that test on the
    controller with a report whose phase is `"???"`, and pytest counts it as
    failed. It is recorded as failed, with the phases seen before the crash
    and none after it, next to every other test."""
    pending: dict[str, PendingResult] = {}
    for when, outcome in _ALL_PHASES_PASSING:
        accumulate(pending, _report(when, outcome, nodeid="t.py::test_ok"))
    for when, outcome in seen_before:
        report = _report(when, outcome, nodeid="t.py::test_crashed", duration=0.5, start=10.0)
        accumulate(pending, _on_worker(report, "gw1"))

    accumulate(pending, _crash("t.py::test_crashed"))

    results = assemble_results(pending)
    assert results.dropped == 0
    ok, crashed = results
    assert ok["node_id"] == "t.py::test_ok"
    assert crashed["node_id"] == "t.py::test_crashed"
    assert crashed["outcome"] == "failed"
    assert crashed["setup_outcome"] == setup_outcome
    assert crashed["call_outcome"] == call_outcome
    assert crashed["teardown_outcome"] is None
    assert crashed["finished_at"] is None
    assert crashed["duration"] == (0.5 * len(seen_before) if seen_before else None)
    assert crashed["started_at"] == ("1970-01-01T00:00:10.000000+00:00" if seen_before else None)
    assert crashed["worker_id"] == "gw1"
    # Without `--vantage-failure-text` nothing attaches evidence to it.
    assert "failure_message" not in crashed


def test_a_crash_report_carries_the_evidence_attached_to_it() -> None:
    """The crash report's own evidence -- its text, attached on the
    controller when failure text was asked for -- is the result's."""
    crash = _crash("t.py::test_crashed")
    crash.vantage_evidence = {"failure_message": str(crash.longrepr)}  # type: ignore[attr-defined]
    setup = _on_worker(_report("setup", "passed", nodeid="t.py::test_crashed"), "gw1")

    result = build_result("t.py::test_crashed", _pending(setup, crash))

    assert result is not None
    assert result["failure_message"] == "worker 'gw1' crashed while running 't.py::test_crashed'"


def test_a_new_setup_report_after_a_crash_starts_a_fresh_attempt() -> None:
    """A crash ends one attempt, not the test: a later attempt that runs
    whole is what is recorded."""
    pending = _pending(
        _on_worker(_report("setup", "passed"), "gw1"),
        _crash("test_nid.py::test_it"),
        *(_on_worker(_report(when, outcome), "gw1") for when, outcome in _ALL_PHASES_PASSING),
    )

    result = build_result("test_nid.py::test_it", pending)

    assert result is not None
    assert result["outcome"] == "passed"
    assert result["teardown_outcome"] == "passed"


def _rerun(when: _Phase, *, duration: float) -> pytest.TestReport:
    """A failed phase report a rerun plugin (pytest-rerunfailures) logs as
    an intermediate attempt: it rewrites the outcome to `"rerun"` before
    logging it and skips the rest of that attempt."""
    report = _report(when, "failed", duration=duration)
    report.outcome = "rerun"  # type: ignore[assignment]  # what the rerun plugin does
    return report


@pytest.mark.parametrize(
    ("attempts", "expected_outcome"),
    [
        pytest.param(
            [
                _report("setup", "passed", duration=1.0),
                _rerun("call", duration=5.0),
                _report("setup", "failed", duration=0.25),
                _report("teardown", "passed", duration=0.5),
            ],
            "error",
            id="retry-fails-in-setup-after-a-rerun-call",
        ),
        pytest.param(
            [
                _report("setup", "passed", duration=1.0),
                _rerun("call", duration=5.0),
                _report("setup", "skipped", duration=0.25),
                _report("teardown", "passed", duration=0.5),
            ],
            "skipped",
            id="retry-skips-in-setup-after-a-rerun-call",
        ),
        pytest.param(
            [
                _report("setup", "passed", duration=1.0),
                _report("call", "passed", duration=5.0),
                _rerun("teardown", duration=2.0),
                _report("setup", "failed", duration=0.25),
                _report("teardown", "passed", duration=0.5),
            ],
            "error",
            id="retry-fails-in-setup-after-a-passed-call",
        ),
    ],
)
def test_a_new_setup_report_starts_a_fresh_attempt(
    attempts: list[pytest.TestReport], expected_outcome: str
) -> None:
    """A rerun plugin runs the same node id again, starting with a new setup
    report. Nothing from the earlier attempt may survive into the recorded
    one: a stale call report would carry the old attempt's outcome, duration
    and output, and a `"rerun"` phase outcome is outside the vocabulary the
    server accepts, so it would reject the whole session."""
    pending: dict[str, PendingResult] = {}
    for report in attempts:
        accumulate(pending, report)

    (result,) = assemble_results(pending)

    assert result["outcome"] == expected_outcome
    assert result["call_outcome"] is None
    assert result["call_duration"] is None
    assert result["duration"] == 0.75  # the final attempt's setup and teardown only


def _subtest(outcome: _ReportOutcome, evidence: dict[str, object]) -> pytest.TestReport:
    """A subtest's report, duck-typed the way the plugin recognises one: the
    parent's node id, `when="call"`, and the subtest's `context`."""
    report = _report("call", outcome)
    report.context = SimpleNamespace(msg=None, kwargs={"i": "2"})  # type: ignore[attr-defined]
    report.vantage_evidence = evidence  # type: ignore[attr-defined]
    return report


def test_a_failed_subtest_fails_its_passing_test_and_supplies_the_evidence() -> None:
    """Subtest reports carry the parent's node id and `when="call"`, and the
    parent's own call report arrives after them. They must not take the
    call slot, or the parent's verdict overwrites a failing subtest's. As
    pytest does, a passing test containing a failed subtest is failed; its
    call report has no exception of its own, so the first failing
    subtest's evidence is recorded."""
    call = _report("call", "passed")
    call.vantage_evidence = {"captured_stdout": "", "captured_stderr": ""}  # type: ignore[attr-defined]
    pending = _pending(
        _report("setup", "passed"),
        _subtest("passed", {"captured_stdout": "", "captured_stderr": ""}),
        _subtest(
            "failed",
            {
                "failure_type": "AssertionError",
                "failure_message": "AssertionError: first",
                "captured_stdout": "",
                "captured_stderr": "",
            },
        ),
        _subtest("failed", {"failure_type": "ValueError", "failure_message": "ValueError: second"}),
        call,
        _report("teardown", "passed"),
    )

    result = build_result("test_nid.py::test_it", pending)

    assert result is not None
    assert result["outcome"] == "failed"
    assert result["call_outcome"] == "passed"
    assert result["failure_type"] == "AssertionError"
    assert result["failure_message"] == "AssertionError: first"


def test_passing_and_skipped_subtests_leave_the_verdict_alone() -> None:
    pending = _pending(
        _report("setup", "passed"),
        _subtest("passed", {}),
        _subtest("skipped", {}),
        _report("call", "passed"),
        _report("teardown", "passed"),
    )

    result = build_result("test_nid.py::test_it", pending)

    assert result is not None
    assert result["outcome"] == "passed"


def test_subtest_output_is_recorded_between_setup_and_call() -> None:
    """Output printed inside a subtest block is captured on the subtest's
    own report, not the parent's call report."""
    reports = [
        _report("setup", "passed"),
        _subtest("passed", {}),
        _report("call", "passed"),
        _report("teardown", "passed"),
    ]
    for report, text in zip(reports, ("S", "U", "C", "T"), strict=True):
        report.vantage_evidence = {"captured_stdout": text, "captured_stderr": ""}  # type: ignore[attr-defined]

    result = build_result("test_nid.py::test_it", _pending(*reports))

    assert result is not None
    assert result["captured_stdout"] == "SUCT"


def test_an_entry_that_cannot_be_built_costs_only_itself() -> None:
    """One report shape the plugin does not expect must cost that test its
    result, never the rest of the session's; how many were dropped is
    exposed so the caller can say so."""
    pending: dict[str, PendingResult] = {}
    accumulate(pending, _report("teardown", "passed", nodeid="t.py::test_no_setup"))
    for when, outcome in _ALL_PHASES_PASSING:
        accumulate(pending, _report(when, outcome, nodeid="t.py::test_ok"))

    results = assemble_results(pending)

    assert [result["node_id"] for result in results] == ["t.py::test_ok"]
    assert results.dropped == 1


def test_a_test_whose_call_never_ran_is_left_out_without_error() -> None:
    """`--setup-only` and `--setup-plan` run setup and teardown but never the
    test itself. There is no verdict to record, which is not an error."""
    pending: dict[str, PendingResult] = {}
    accumulate(pending, _report("setup", "passed"))
    accumulate(pending, _report("teardown", "passed"))

    results = assemble_results(pending)

    assert results == []
    assert results.dropped == 0


def test_assemble_results_preserves_execution_order() -> None:
    """A `dict` gives insertion order for free, so the emitted array is in
    execution order -- and skips any entry with no teardown report (the
    same drop `build_result` proves directly above)."""
    pending: dict[str, PendingResult] = {}
    for when, outcome in _ALL_PHASES_PASSING[:2]:  # setup+call only -- dropped
        accumulate(pending, _report(when, outcome, nodeid="test_a.py::test_unresolved"))
    for node_id in ("test_a.py::test_1", "test_a.py::test_2", "test_a.py::test_3"):
        for when, outcome in _ALL_PHASES_PASSING:
            accumulate(pending, _report(when, outcome, nodeid=node_id))

    results = assemble_results(pending)

    assert [result["node_id"] for result in results] == [
        "test_a.py::test_1",
        "test_a.py::test_2",
        "test_a.py::test_3",
    ]


# --- worker_id through a getattr chain, never xdist -------------------------


@pytest.mark.parametrize(
    ("worker_id_attr", "node_attr", "expected"),
    [
        pytest.param("gw1", None, "gw1", id="reads-the-reports-own-attribute-first"),
        pytest.param(
            None,
            SimpleNamespace(gateway=SimpleNamespace(id="gw2")),
            "gw2",
            id="falls-back-to-the-node-gateway-id-chain",
        ),
        pytest.param(None, None, None, id="none-without-xdist"),
    ],
)
def test_build_result_worker_id(
    worker_id_attr: str | None, node_attr: object, expected: str | None
) -> None:
    """A `getattr` chain, never an xdist import: `report.worker_id` first --
    what xdist sets directly on a forwarded report -- then
    `report.node.gateway.id` as a fallback, simulated here with a bare
    stand-in unrelated to the real xdist classes. `None` when neither is
    present.
    """
    reports = [_report(when, outcome) for when, outcome in _ALL_PHASES_PASSING]
    for report in reports:
        if worker_id_attr is not None:
            report.worker_id = worker_id_attr  # type: ignore[attr-defined]
        if node_attr is not None:
            report.node = node_attr  # type: ignore[attr-defined]

    result = build_result("test_nid.py::test_it", _pending(*reports))

    assert result is not None
    assert result["worker_id"] == expected


# --- evidence-phase selection -------------------------------------------------

# Which phase's report `_select_evidence_phase` must return for each of
# `derive_outcome`'s nine rows above (error: setup-if-failed else teardown;
# failed/xfailed: call; skipped: setup-if-skipped else call; xpassed/passed:
# none).
_EXPECTED_EVIDENCE_PHASE = [
    "setup",  # row 1: error, setup failed
    "setup",  # row 2: skipped, setup skipped
    "call",  # row 3: xfailed
    "call",  # row 4: skipped, call skipped (bare skip after passed setup)
    "call",  # row 5: xfailed
    "call",  # row 6: failed
    None,  # row 7: xpassed
    "teardown",  # row 8: error, teardown failed after a passed call
    None,  # row 9: passed
]


@pytest.mark.parametrize(
    ("setup_outcome", "call_spec", "teardown_outcome", "expected_outcome", "expected_phase"),
    [(*row, phase) for row, phase in zip(_OUTCOME_ROWS, _EXPECTED_EVIDENCE_PHASE, strict=True)],
    ids=[f"row-{n}" for n in range(1, 10)],
)
def test_evidence_phase_selection_matches_the_derived_outcome_table(
    setup_outcome: _ReportOutcome,
    call_spec: tuple[_ReportOutcome, str | None] | None,
    teardown_outcome: _ReportOutcome,
    expected_outcome: str,
    expected_phase: _Phase | None,
) -> None:
    """Evidence-phase selection, evaluated against every one of
    `derive_outcome`'s nine rows -- the selection is keyed off the DERIVED
    outcome, never restated as its own condition."""
    setup = _report("setup", setup_outcome)
    call = _report("call", call_spec[0], wasxfail=call_spec[1]) if call_spec else None
    teardown = _report("teardown", teardown_outcome)

    outcome = derive_outcome(setup, call, teardown)
    assert outcome == expected_outcome  # the table above is keyed off this value

    selected = _select_evidence_phase(setup, call, teardown, outcome)

    expected_report = {"setup": setup, "call": call, "teardown": teardown, None: None}[
        expected_phase
    ]
    assert selected is expected_report


# --- captured output concatenation, no marker ------------------------------


def test_captured_output_concatenates_phases_in_order_no_marker() -> None:
    """`captured_stdout`/`captured_stderr` are the concatenation of every
    phase that ran, in setup->call->teardown order, with NO delimiter --
    in-band phase headers are forgeable by a test that prints that exact
    line. Every phase contributes, not just the one `_select_evidence_phase`
    would pick for a failure.
    """
    setup = _report("setup", "passed")
    setup.vantage_evidence = {  # type: ignore[attr-defined]
        "captured_stdout": "SETUP_OUT",
        "captured_stderr": "SETUP_ERR",
    }
    call = _report("call", "passed")
    call.vantage_evidence = {  # type: ignore[attr-defined]
        "captured_stdout": "CALL_OUT",
        "captured_stderr": "CALL_ERR",
    }
    teardown = _report("teardown", "passed")
    teardown.vantage_evidence = {  # type: ignore[attr-defined]
        "captured_stdout": "TEARDOWN_OUT",
        "captured_stderr": "TEARDOWN_ERR",
    }
    result = build_result("test_nid.py::test_it", _pending(setup, call, teardown))

    assert result is not None
    assert result["captured_stdout"] == "SETUP_OUTCALL_OUTTEARDOWN_OUT"
    assert result["captured_stderr"] == "SETUP_ERRCALL_ERRTEARDOWN_ERR"


@pytest.mark.parametrize(
    ("per_phase", "expected"),
    [
        pytest.param(("S", "C", None), "SC", id="one-unreadable-phase-drops-only-itself"),
        pytest.param((None, None, None), None, id="capture-disabled-stays-null"),
    ],
)
def test_captured_output_is_null_only_when_no_phase_was_read(
    per_phase: tuple[str | None, str | None, str | None], expected: str | None
) -> None:
    """A `None` in one phase means that phase's buffer could not be read; the
    field is null for the whole result only when every phase is `None`, which
    is what a session with capture disabled produces."""
    reports = []
    for when, value in zip(("setup", "call", "teardown"), per_phase, strict=True):
        report = _report(when, "passed")  # type: ignore[arg-type]
        report.vantage_evidence = {  # type: ignore[attr-defined]
            "captured_stdout": value,
            "captured_stderr": value,
        }
        reports.append(report)
    result = build_result("test_nid.py::test_it", _pending(*reports))

    assert result is not None
    assert result["captured_stdout"] == expected
    assert result["captured_stderr"] == expected


# --- the finish report carries the assembled results -------------------------


def test_the_finish_report_carries_the_assembled_results_in_execution_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pytest_runtest_logreport` only accumulates, sending nothing;
    `pytest_sessionfinish` sends the finish report carrying the assembled
    `results` array, in execution order. No session start is driven here,
    so the finish report is the only report sent."""
    from pytest_vantage.recorder import Recorder

    sent: list[dict[str, object]] = []

    def _capture(address: str, report: dict[str, object], *, timeout: float) -> None:
        sent.append(report)

    monkeypatch.setattr("pytest_vantage.recorder.send", _capture)
    # This test is about assembled results in the finish-write, not vcs
    # capture -- neutralise it rather than spawning a real `git` subprocess
    # for something this test does not exercise.
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())
    recorder = Recorder(
        config=SimpleNamespace(rootpath="unused"),  # type: ignore[arg-type]
        address="http://example.invalid",
        timeout=1.0,
        lifecycle_available=True,
    )

    for report_ in (
        _report("setup", "passed", nodeid="test_a.py::test_1"),
        _report("call", "passed", nodeid="test_a.py::test_1"),
        _report("teardown", "passed", nodeid="test_a.py::test_1"),
        _report("setup", "passed", nodeid="test_a.py::test_2"),
        _report("call", "failed", nodeid="test_a.py::test_2"),
        _report("teardown", "passed", nodeid="test_a.py::test_2"),
    ):
        recorder.pytest_runtest_logreport(report_)

    recorder.pytest_sessionfinish(
        session=SimpleNamespace(shouldfail=False, shouldstop=False),  # type: ignore[arg-type]
        exitstatus=1,
    )

    assert len(sent) == 1
    (report,) = sent
    results = report["results"]
    assert isinstance(results, list)
    assert [entry["node_id"] for entry in results] == ["test_a.py::test_1", "test_a.py::test_2"]
    assert results[1]["outcome"] == "failed"
