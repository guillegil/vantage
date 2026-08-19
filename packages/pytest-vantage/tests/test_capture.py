"""Table-driven tests for `pytest_vantage.capture` (design.md D16-D18, RQ-9):
identity decomposition, plus -- from Phase 7 -- phase accumulation, outcome
derivation and result payload assembly. Pure functions over data, no
subprocess or server needed; real `pytest.TestReport` instances stand in for
what `Recorder` receives at runtime.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal

import pytest
from pytest_vantage.capture import (
    _Pending,
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
    runtime (design.md D16/D17). `wasxfail` is set only when given, matching
    pytest's own dynamic-attribute behaviour: present or absent, never
    `None` -- the `hasattr` check D17 requires depends on this.
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


@pytest.mark.req(id="RQ-9")
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
    """RQ-9.2: a module-level test's `class_name` is `None`, never `""`.
    A nested class's segments join with `"::"`. RQ-9.3: an unparametrised
    test's `param_id` is `None`.
    """
    identity = decompose(node_id)

    assert identity.file_path == expected_file_path
    assert identity.class_name == expected_class_name
    assert identity.function_name == expected_function_name
    assert identity.param_id == expected_param_id
    if expected_param_id is None:
        assert identity.param_id is None  # None must stay None, never a computed ""


@pytest.mark.req(id="RQ-9")
def test_decompose_identity_empty_brackets_is_empty_string_not_none() -> None:
    """The brackets are the evidence of parametrisation; their content is
    not (design.md D18). This is not a hypothetical case -- this exact node
    id exists in this repository today:
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


@pytest.mark.req(id="RQ-9")
def test_decompose_identity_slices_on_first_and_last_bracket_not_partition_symmetry() -> None:
    """A parameter id that itself contains brackets must survive intact.
    Slicing on the first `"["` and the last `"]"` gets this right; a
    symmetric `partition`/`rpartition` split would not (design.md D18).
    """
    node_id = "packages/vantage/tests/test_x.py::test_x[[0]]"

    identity = decompose(node_id)

    assert identity.function_name == "test_x"
    assert identity.param_id == "[0]"


@pytest.mark.req(id="RQ-9")
def test_decompose_identity_directory_containing_brackets_is_not_mistaken_for_a_parameter() -> None:
    """A directory component may itself contain brackets (design.md D18
    addendum). The parameter section must be located within the remainder
    AFTER the file path has already been split off -- searching the whole
    `node_id` for a bracket would misidentify this directory as the start
    of a parameter section and corrupt every field that follows it.
    """
    node_id = "tests/data[1]/test_a.py::test_b[x]"

    identity = decompose(node_id)

    assert identity.file_path == "tests/data[1]/test_a.py"
    assert identity.class_name is None
    assert identity.function_name == "test_b"
    assert identity.param_id == "x"


@pytest.mark.req(id="RQ-9")
def test_decompose_identity_rejects_a_string_with_no_double_colon_at_all() -> None:
    """Every real pytest node id has at least one `"::"` separating the
    file path from the test path (design.md D18 addendum). A string with
    none is not a node id at all, and silently treating the whole string
    as a function name (or as a bare file path with no test) would hide
    that malformed input rather than surface it -- so this raises instead.
    """
    with pytest.raises(ValueError, match="::"):
        decompose("not_a_node_id_at_all.py")


# --- Phase 7, task 7.1: all nine D17 precedence rows -------------------------

# (setup_outcome, call_spec, teardown_outcome, expected_outcome); call_spec is
# (call_outcome, wasxfail) or None when setup did not pass (call never runs).
_D17Row = tuple[_ReportOutcome, "tuple[_ReportOutcome, str | None] | None", _ReportOutcome, str]
_D17_ROWS: list[_D17Row] = [
    ("failed", None, "passed", "error"),  # row 1, RQ-4.1
    ("skipped", None, "passed", "skipped"),  # row 2, RQ-4.2
    ("passed", ("skipped", "expected"), "passed", "xfailed"),  # row 3, RQ-4.3
    ("passed", ("skipped", None), "passed", "skipped"),  # row 4
    ("passed", ("failed", "expected"), "passed", "xfailed"),  # row 5, RQ-4.3
    ("passed", ("failed", None), "passed", "failed"),  # row 6
    ("passed", ("passed", "expected"), "passed", "xpassed"),  # row 7, RQ-4.4
    ("passed", ("passed", None), "failed", "error"),  # row 8, RQ-4.5
    ("passed", ("passed", None), "passed", "passed"),  # row 9
]


@pytest.mark.req(id="RQ-4")
@pytest.mark.parametrize(
    ("setup_outcome", "call_spec", "teardown_outcome", "expected_outcome"),
    _D17_ROWS,
    ids=[f"row-{n}" for n in range(1, 10)],
)
def test_derive_outcome_all_nine_d17_precedence_rows(
    setup_outcome: _ReportOutcome,
    call_spec: tuple[_ReportOutcome, str | None] | None,
    teardown_outcome: _ReportOutcome,
    expected_outcome: str,
) -> None:
    """design.md D17's nine-row precedence table, evaluated in order."""
    setup = _report("setup", setup_outcome)
    call = _report("call", call_spec[0], wasxfail=call_spec[1]) if call_spec else None
    teardown = _report("teardown", teardown_outcome)

    assert derive_outcome(setup, call, teardown) == expected_outcome


# --- Phase 7, task 7.2: a strict xfail that passes is failed, not xpassed ----


@pytest.mark.req(id="RQ-4")
def test_derive_outcome_strict_xfail_that_passes_is_failed_not_xpassed() -> None:
    """pytest sets `outcome="failed"` with `wasxfail` entirely ABSENT for
    `[XPASS(strict)]` (verified against `_pytest/skipping.py`'s
    `pytest_runtest_makereport`) -- RQ-4.4 covers only the non-strict case.
    """
    setup = _report("setup", "passed")
    call = _report("call", "failed")  # no wasxfail -- this IS the strict-xpass shape
    teardown = _report("teardown", "passed")

    assert derive_outcome(setup, call, teardown) == "failed"


# --- Phase 7, task 7.3: teardown downgrades only a passed result -------------

# Row 8 (in the table above) already proves the positive case -- `passed` IS
# downgraded. This proves the negative: every OTHER verdict keeps its own
# word despite the same failing teardown.
_KEEPS_OWN_WORD: list[tuple[_ReportOutcome, str | None, str]] = [
    ("failed", None, "failed"),
    ("skipped", "expected", "xfailed"),
    ("passed", "expected", "xpassed"),
    ("skipped", None, "skipped"),
]


@pytest.mark.req(id="RQ-4")
@pytest.mark.parametrize(
    ("call_outcome", "call_wasxfail", "expected_outcome"),
    _KEEPS_OWN_WORD,
    ids=["failed", "xfailed", "xpassed", "skipped"],
)
def test_derive_outcome_teardown_failure_downgrades_only_a_passed_result(
    call_outcome: _ReportOutcome, call_wasxfail: str | None, expected_outcome: str
) -> None:
    """Row 8's downgrade to `error` applies ONLY to row 9's `passed`
    (design.md D17); every other verdict keeps its own word."""
    setup = _report("setup", "passed")
    call = _report("call", call_outcome, wasxfail=call_wasxfail)
    teardown = _report("teardown", "failed")

    assert derive_outcome(setup, call, teardown) == expected_outcome


# --- Phase 7, task 7.4: the JSON hop -- null vs zero -------------------------


@pytest.mark.req(id="RQ-5")
def test_build_result_phase_duration_null_vs_zero_survives_the_json_hop() -> None:
    """A phase that never ran serialises as JSON `null`, never `0.0`; a
    genuine `0.0` survives as `0.0` -- never `x or None` (design.md D17),
    checked at the actual `json.dumps` boundary, not just the Python dict.
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


# --- Phase 7, task 7.5: resolution, not attendance ---------------------------


@pytest.mark.req(id="RQ-4")
def test_build_result_returns_none_when_teardown_was_never_seen() -> None:
    """design.md D16, "resolution, not attendance": setup+call with no
    teardown (e.g. interrupted mid-session) is DROPPED, not invented --
    `assemble_results` relies on this `None` to skip the entry (D16)."""
    pending = _Pending()
    pending.setup = _report("setup", "passed")
    pending.call = _report("call", "passed")
    # No teardown report was ever seen.

    assert build_result("test_nid.py::test_it", pending) is None


# --- Phase 7, task 7.6: accumulation mechanics -------------------------------


def test_accumulate_overwrites_a_duplicate_report_for_the_same_phase() -> None:
    """design.md D16: a duplicate report for the same node id and phase
    overwrites, never a second row -- D19 layer 1's supporting mechanism."""
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
    """design.md D16: a `dict` gives insertion order for free, so the
    emitted array is in execution order -- and skips any entry with no
    teardown report (the same drop `build_result` proves directly above)."""
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


# --- Phase 7, task 7.6: worker_id through a getattr chain, never xdist ------


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
    """A `getattr` chain, never an xdist import (RQ-24, design.md D16):
    `report.worker_id` first -- what xdist sets directly on a forwarded
    report -- then `report.node.gateway.id` as a fallback, simulated here
    with a bare stand-in unrelated to the real xdist classes. `None` when
    neither is present.
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


# --- Phase 7, task 7.7: exactly one HTTP request per session ----------------


def test_recorder_sends_the_assembled_results_in_the_one_session_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """design.md D16, RQ-25.2: `pytest_runtest_logreport` only accumulates;
    `pytest_sessionfinish` still sends once, now carrying the assembled
    `results` array."""
    from pytest_vantage.recorder import Recorder

    sent: list[dict[str, object]] = []

    def _capture(address: str, report: dict[str, object], *, timeout: float) -> None:
        sent.append(report)

    monkeypatch.setattr("pytest_vantage.recorder.send", _capture)
    recorder = Recorder(
        config=None,  # type: ignore[arg-type]
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
