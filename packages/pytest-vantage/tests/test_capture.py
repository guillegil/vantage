"""Table-driven tests for `pytest_vantage.capture`: identity decomposition,
phase accumulation, outcome derivation and result payload assembly. Pure
functions over data, no subprocess or server needed; real `pytest.TestReport`
instances stand in for what `Recorder` receives at runtime.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal

import pytest
from pytest_vantage import vcs
from pytest_vantage.capture import (
    _Pending,
    _select_evidence_phase,
    accumulate,
    assemble_results,
    build_result,
    decompose,
    derive_outcome,
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


def test_decompose_identity_rejects_a_string_with_no_double_colon_at_all() -> None:
    """Every real pytest node id has at least one `"::"` separating the
    file path from the test path. A string with none is not a node id at
    all, and silently treating the whole string as a function name (or as a
    bare file path with no test) would hide that malformed input rather
    than surface it -- so this raises instead.
    """
    with pytest.raises(ValueError, match="::"):
        decompose("not_a_node_id_at_all.py")


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
    pending = _Pending()
    pending.setup = _report("setup", "failed", duration=0.0)  # ran, genuinely instant
    pending.teardown = _report("teardown", "passed", duration=0.0031)
    # pending.call is deliberately never set -- setup failed, so call never ran.

    result = build_result("test_nid.py::test_it", pending)
    assert result is not None

    serialised = json.dumps(result)
    payload = json.loads(serialised)

    assert payload["setup_duration"] == 0.0  # genuine zero survives
    assert payload["call_duration"] is None  # call never ran -- null, not 0.0
    assert payload["call_outcome"] is None
    assert '"call_duration": null' in serialised
    assert '"setup_duration": 0.0' in serialised


# --- a result without a teardown report is dropped -------------------------


def test_build_result_returns_none_when_teardown_was_never_seen() -> None:
    """Setup+call with no teardown (e.g. interrupted mid-session) is
    DROPPED, not invented -- `assemble_results` relies on this `None` to skip
    the entry."""
    pending = _Pending()
    pending.setup = _report("setup", "passed")
    pending.call = _report("call", "passed")
    # No teardown report was ever seen.

    assert build_result("test_nid.py::test_it", pending) is None


# --- accumulation mechanics -------------------------------------------------


def test_accumulate_overwrites_a_duplicate_report_for_the_same_phase() -> None:
    """A duplicate report for the same node id and phase overwrites, never a
    second row."""
    pending: dict[str, _Pending] = {}
    first_call = _report("call", "failed", nodeid="test_nid.py::test_it")
    second_call = _report("call", "passed", nodeid="test_nid.py::test_it")

    accumulate(pending, first_call)
    accumulate(pending, second_call)

    assert len(pending) == 1
    assert pending["test_nid.py::test_it"].call is second_call


_ALL_PHASES_PASSING: tuple[tuple[_Phase, _ReportOutcome], ...] = (
    ("setup", "passed"),
    ("call", "passed"),
    ("teardown", "passed"),
)


def test_assemble_results_preserves_execution_order() -> None:
    """A `dict` gives insertion order for free, so the emitted array is in
    execution order -- and skips any entry with no teardown report (the
    same drop `build_result` proves directly above)."""
    pending: dict[str, _Pending] = {
        "test_a.py::test_unresolved": _Pending(),  # setup+call only -- dropped
    }
    pending["test_a.py::test_unresolved"].setup = _report("setup", "passed")
    pending["test_a.py::test_unresolved"].call = _report("call", "passed")
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
    pending = _Pending()
    pending.setup = _report("setup", "passed")
    if worker_id_attr is not None:
        pending.setup.worker_id = worker_id_attr  # type: ignore[attr-defined]
    if node_attr is not None:
        pending.setup.node = node_attr  # type: ignore[attr-defined]
    pending.call = _report("call", "passed")
    pending.teardown = _report("teardown", "passed")

    result = build_result("test_nid.py::test_it", pending)

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
    pending = _Pending()
    pending.setup = setup
    pending.call = call
    pending.teardown = teardown

    result = build_result("test_nid.py::test_it", pending)

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
    pending = _Pending()
    pending.setup, pending.call, pending.teardown = reports

    result = build_result("test_nid.py::test_it", pending)

    assert result is not None
    assert result["captured_stdout"] == expected
    assert result["captured_stderr"] == expected


# --- exactly one HTTP request per session ----------------------------------


def test_recorder_sends_the_assembled_results_in_the_one_session_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pytest_runtest_logreport` only accumulates; `pytest_sessionfinish`
    sends once, carrying the assembled `results` array."""
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

    recorder.pytest_sessionfinish(exitstatus=1)

    assert len(sent) == 1
    (report,) = sent
    results = report["results"]
    assert isinstance(results, list)
    assert [entry["node_id"] for entry in results] == ["test_a.py::test_1", "test_a.py::test_2"]
    assert results[1]["outcome"] == "failed"
