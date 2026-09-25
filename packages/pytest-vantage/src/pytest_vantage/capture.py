"""Node-id decomposition, phase accumulation, outcome derivation and result
payload assembly for the plugin.

`vantage.core.domain.result.CaseIdentity`/`Result` are the shapes this
information eventually takes, but the plugin must stay installable without
the server, so it cannot import `vantage` and owns plain stdlib structures
instead. Standard library and `pytest` only -- never `xdist`
(`test_plugin_imports.py` is the guard).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

import pytest


class DecomposedIdentity(NamedTuple):
    """A test's identity, decomposed from its pytest node id.

    ``class_name`` is `None` for a module-level test, never `""`.
    ``param_id`` is `None` for an unparametrised test and `""` for a
    parametrised test whose parameter id is itself the empty string -- the
    brackets are the evidence of parametrisation, not their content. Never
    write ``x or None`` here: it would turn a genuine ``""`` into ``None``.
    """

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None


def decompose(node_id: str) -> DecomposedIdentity:
    """Split a pytest node id into its identity components.

    The parameter section (a parametrised test's ``[...]`` suffix) is
    arbitrary user data supplied to ``@pytest.mark.parametrize`` -- it MAY
    itself contain ``"::"`` (``test_p[a::b]``) or brackets
    (``test_p[[0]]``). It is therefore removed from the remainder BEFORE
    that remainder is split on ``"::"`` to find the class and function
    segments -- do NOT re-simplify this back into one ``node_id.split("::")``
    call, which would misparse the parameter section's own ``"::"`` as a
    class separator.

    Likewise a directory component MAY itself contain brackets
    (``tests/data[1]/test_a.py::test_b[x]``), so the parameter section is
    located only within the remainder AFTER the file path has already been
    split off -- never by searching the whole ``node_id`` for a bracket.

    Steps:

    1. Partition on the FIRST ``"::"`` only. The part before it is
       ``file_path``; pytest node ids do not put ``"::"`` inside a file
       path, so this is unambiguous. A ``node_id`` with no ``"::"`` at all
       belongs to an item that is itself a file (a node that is both
       ``pytest.File`` and ``pytest.Item``, which pytest warns about but
       runs): the whole id is ``file_path`` and the file's name is
       ``function_name``.
    2. In that remainder: if it ends with ``"]"`` and contains ``"["``,
       slice on the FIRST ``"["`` and the LAST ``"]"`` -- not
       ``partition``/``rpartition`` symmetry -- to pull out ``param_id``,
       then drop that suffix from the remainder. Otherwise ``param_id`` is
       ``None`` and the remainder is left untouched.
    3. Split what is left on ``"::"``. The last segment is
       ``function_name``; every segment before it, joined with ``"::"``,
       is ``class_name`` -- ``None`` when there are none (module-level
       test).
    """
    file_path, separator, remainder = node_id.partition("::")
    if not separator:
        return DecomposedIdentity(
            node_id=node_id,
            file_path=node_id,
            class_name=None,
            function_name=node_id.rpartition("/")[2],
            param_id=None,
        )

    if remainder.endswith("]") and "[" in remainder:
        bracket_start = remainder.index("[")
        param_id = remainder[bracket_start + 1 : -1]
        remainder = remainder[:bracket_start]
    else:
        param_id = None

    segments = remainder.split("::")
    class_segments = segments[:-1]
    class_name = "::".join(class_segments) if class_segments else None
    function_name = segments[-1]

    return DecomposedIdentity(
        node_id=node_id,
        file_path=file_path,
        class_name=class_name,
        function_name=function_name,
        param_id=param_id,
    )


def _isoformat_utc(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC text, matching `recorder.py`'s
    `isoformat_utc` exactly -- duplicated rather than imported, since
    `recorder.py` imports from this module and the reverse would be circular.
    """
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


# The only outcomes pytest itself gives a phase report. A rerun plugin logs an
# intermediate attempt as "rerun"; that is not a result, and the server
# rejects any phase outcome outside its vocabulary.
_REPORT_OUTCOMES = frozenset({"passed", "failed", "skipped"})


# How much each verdict says went wrong, for choosing between several
# complete executions of one node id.
_SEVERITY = {"skipped": 0, "xfailed": 1, "passed": 2, "xpassed": 3, "failed": 4, "error": 5}


class _Execution:
    """The up-to-three reports pytest emits for one execution of a test: one
    attempt, on one worker. A duplicate report for the same phase overwrites
    rather than duplicates, so a report delivered twice never becomes a
    second result.
    """

    __slots__ = ("setup", "call", "teardown")

    def __init__(self) -> None:
        self.setup: pytest.TestReport | None = None
        self.call: pytest.TestReport | None = None
        self.teardown: pytest.TestReport | None = None

    def record(self, report: pytest.TestReport) -> None:
        """Explicit dispatch on the three phases. Any other `when` is ignored:
        xdist logs a test whose worker crashed with `when="???"`, and that
        report is no phase of a result.
        """
        when = report.when
        if when == "setup":
            # A setup report starts a new attempt at the test (a rerun plugin
            # retrying it), so nothing from an earlier attempt may survive.
            self.setup = self.call = self.teardown = None
        if report.outcome not in _REPORT_OUTCOMES:
            return
        if when == "setup":
            self.setup = report
        elif when == "call":
            self.call = report
        elif when == "teardown":
            self.teardown = report


class _Pending:
    """Every report pytest emitted for one node id, keyed by node id in
    `Recorder._results`; a `dict[str, _Pending]` gives insertion order for
    free.

    Reports are grouped by the xdist worker that sent them: under `--dist
    each` every worker runs every test and the controller receives their
    reports interleaved, and phases from different workers must never be
    stitched into one result. Without xdist the worker id is `None` and
    there is one execution.
    """

    __slots__ = ("executions",)

    def __init__(self) -> None:
        self.executions: dict[str | None, _Execution] = {}

    def record(self, report: pytest.TestReport) -> None:
        self.executions.setdefault(_worker_id(report), _Execution()).record(report)


def accumulate(pending: dict[str, _Pending], report: pytest.TestReport) -> None:
    """Record one phase report into `pending`, keyed by `report.nodeid`.
    Called from `Recorder.pytest_runtest_logreport`, once per phase report
    pytest fires -- never sends anything itself."""
    pending.setdefault(report.nodeid, _Pending()).record(report)


def derive_outcome(
    setup: pytest.TestReport, call: pytest.TestReport | None, teardown: pytest.TestReport
) -> str:
    """Derive the overall outcome from the three phase reports; the checks
    run in a fixed precedence order. `call` is `None` only when
    `setup.outcome` is not `"passed"` (pytest never runs call after a failed
    or skipped setup).

    Two consequences easy to get wrong: a teardown failure downgrades ONLY a
    `passed` call result to `error` -- every other verdict keeps its own
    word. A **strict** `xfail` that passes has `outcome="failed"` with
    `wasxfail` entirely absent (see `_pytest/skipping.py`), so it is
    `failed`, not `xpassed`. `wasxfail`'s presence (`hasattr`), never its
    truthiness, is what matters -- pytest sometimes sets the reason to `""`.
    """
    if setup.outcome == "failed":
        return "error"
    if setup.outcome == "skipped":
        return "skipped"
    if call is None:
        raise AssertionError("setup passed but no call report was recorded")
    if call.outcome == "skipped":
        return "xfailed" if hasattr(call, "wasxfail") else "skipped"
    if call.outcome == "failed":
        return "xfailed" if hasattr(call, "wasxfail") else "failed"
    if hasattr(call, "wasxfail"):
        return "xpassed"
    if teardown.outcome == "failed":
        return "error"
    return "passed"


def _phase_duration(report: pytest.TestReport | None) -> float | None:
    """`None` when the phase never ran, never `0.0`. Plain attribute access,
    never `report.duration or None`, which would turn a genuine `0.0` into
    `None`.
    """
    if report is None:
        return None
    return report.duration


def _phase_timestamp(report: pytest.TestReport, attribute: str) -> str | None:
    """`getattr(report, "start"/"stop", None)` -- epoch floats on pytest
    >= 8 -- via `datetime.fromtimestamp(..., timezone.utc)`. Never
    `datetime.UTC`, which needs 3.11 and this project supports 3.10.
    """
    epoch = getattr(report, attribute, None)
    if epoch is None:
        return None
    return _isoformat_utc(datetime.fromtimestamp(epoch, timezone.utc))


def _worker_id(report: pytest.TestReport) -> str | None:
    """A `getattr` chain, never an xdist import, so the plugin works with
    xdist absent: `report.worker_id` first, then `report.node.gateway.id`.
    `None` when neither is present.
    """
    worker_id = getattr(report, "worker_id", None)
    if worker_id is not None:
        return str(worker_id)
    gateway = getattr(getattr(report, "node", None), "gateway", None)
    gateway_id = getattr(gateway, "id", None)
    return str(gateway_id) if gateway_id is not None else None


def _select_evidence_phase(
    setup: pytest.TestReport,
    call: pytest.TestReport | None,
    teardown: pytest.TestReport,
    outcome: str,
) -> pytest.TestReport | None:
    """Which phase report's `vantage_evidence` belongs in the recorded
    result, keyed off the DERIVED outcome, never restated as an independent
    condition:

    | Derived outcome    | Evidence taken from                          |
    | ------------------- | --------------------------------------------- |
    | `error`             | setup if setup failed, else teardown          |
    | `failed`, `xfailed` | call                                          |
    | `skipped`           | setup if setup skipped, else call             |
    | `xpassed`, `passed` | none                                          |

    `call` is `None` only when `setup.outcome` is not `"passed"`
    (`derive_outcome`'s own precondition) -- exactly the cases this
    function never needs `call` for (`error`/`skipped` with a skipped
    setup), so the `pytest.TestReport | None` signature never needs a
    runtime `None` check.
    """
    if outcome == "error":
        return setup if setup.outcome == "failed" else teardown
    if outcome in ("failed", "xfailed"):
        return call
    if outcome == "skipped":
        return setup if setup.outcome == "skipped" else call
    return None  # xpassed, passed


def _captured_output(
    setup: pytest.TestReport,
    call: pytest.TestReport | None,
    teardown: pytest.TestReport,
) -> dict[str, object]:
    """`captured_stdout`/`captured_stderr`, concatenated across every phase
    that ran, in setup->call->teardown order, with no delimiter (an in-band
    phase header could be forged by a test printing that exact line).
    Independent of `_select_evidence_phase`, because captured output is a
    record of what ran, not of what went wrong: even a passing test's output
    is included.

    Absent entirely from the returned dict (never a dict of nulls) when
    `EvidenceCollector` never ran on ANY phase of this test -- e.g. without
    `--vantage-failure-text` -- the same convention `build_result`'s
    evidence merge follows.

    A phase reporting `None` for a field is one of two things: capture was
    disabled for the session (`evidence.py::_captured_fields` then reports
    `None` for every phase), or reading that one phase's output failed. So a
    `None` phase contributes nothing, and the field is `None` for the whole
    result only when no phase could be read at all.
    """
    evidences = [
        evidence
        for report in (setup, call, teardown)
        if report is not None
        for evidence in (getattr(report, "vantage_evidence", None),)
        if isinstance(evidence, dict)
    ]
    if not evidences:
        return {}

    fields: dict[str, object] = {}
    for field in ("captured_stdout", "captured_stderr"):
        parts = [str(value) for value in (e.get(field) for e in evidences) if value is not None]
        fields[field] = "".join(parts) if parts else None
    return fields


def build_result(node_id: str, pending: _Pending) -> dict[str, object] | None:
    """Build one wire-shape `results[]` entry from an accumulated `_Pending`.
    Returns `None` -- dropped, never invented -- when no execution of the
    test was observed whole: a half-observed test (e.g. one interrupted
    mid-call) is worse reported as whole than not at all.

    Several complete executions of one node id come from xdist's `--dist
    each`. The server keeps one result per node id, so the most severe one
    is sent, whole, from the worker that produced it: a failure on one
    worker is never hidden by a pass on another. Ties keep the execution
    seen first.
    """
    results = [
        result
        for result in (_build_execution(node_id, e) for e in pending.executions.values())
        if result is not None
    ]
    if not results:
        return None
    return max(results, key=lambda result: _SEVERITY[str(result["outcome"])])


def _build_execution(node_id: str, execution: _Execution) -> dict[str, object] | None:
    """One execution's `results[]` entry, or `None` when its teardown report
    was never seen, or when setup passed and the test itself never ran
    (`--setup-only`, `--setup-plan`): there is no verdict to record."""
    if execution.teardown is None:
        return None
    setup = execution.setup
    if setup is None:
        raise AssertionError("a teardown report implies a setup report was seen first")
    call = execution.call
    if call is None and setup.outcome == "passed":
        return None
    teardown = execution.teardown

    identity = decompose(node_id)
    outcome = derive_outcome(setup, call, teardown)

    setup_duration = _phase_duration(setup)
    call_duration = _phase_duration(call)
    teardown_duration = _phase_duration(teardown)
    durations = [d for d in (setup_duration, call_duration, teardown_duration) if d is not None]
    duration = sum(durations) if durations else None

    result: dict[str, object] = {
        "node_id": identity.node_id,
        "file_path": identity.file_path,
        "class_name": identity.class_name,
        "function_name": identity.function_name,
        "param_id": identity.param_id,
        "outcome": outcome,
        "duration": duration,
        "started_at": _phase_timestamp(setup, "start"),
        "finished_at": _phase_timestamp(teardown, "stop"),
        "setup_outcome": setup.outcome,
        "call_outcome": call.outcome if call is not None else None,
        "teardown_outcome": teardown.outcome,
        "setup_duration": setup_duration,
        "call_duration": call_duration,
        "teardown_duration": teardown_duration,
        "worker_id": _worker_id(setup),
    }

    # The evidence `EvidenceCollector` attached to the selected phase report,
    # merged in -- absent entirely (never a dict of nulls) when that phase
    # carries none, e.g. without `--vantage-failure-text`.
    evidence_report = _select_evidence_phase(setup, call, teardown, outcome)
    evidence = getattr(evidence_report, "vantage_evidence", None)
    if isinstance(evidence, dict):
        result.update(evidence)

    # Captured output spans ALL phases, not just the one selected above --
    # applied after the evidence merge so it overrides the single-phase
    # captured_stdout/captured_stderr that merge carried in.
    result.update(_captured_output(setup, call, teardown))

    return result


class AssembledResults(list[dict[str, object]]):
    """The `results` array, plus `dropped`: how many tests had reports that
    could not be built into a result. Still a plain list to everything that
    serialises or budgets it.
    """

    dropped: int = 0


def assemble_results(pending: dict[str, _Pending]) -> AssembledResults:
    """Build the `results` array in insertion (execution) order. Entries
    that were never observed whole are left out (`build_result` returning
    `None`).

    Each entry is built on its own: a report shape this module does not
    expect costs that one test its result and is counted in `dropped`,
    never the whole session's finish report.
    """
    results = AssembledResults()
    for entry_node_id, entry in pending.items():
        try:
            result = build_result(entry_node_id, entry)
        except Exception:  # deliberately broad -- one result lost, not the session's
            results.dropped += 1
            continue
        if result is not None:
            results.append(result)
    return results


__all__ = [
    "AssembledResults",
    "DecomposedIdentity",
    "accumulate",
    "assemble_results",
    "build_result",
    "decompose",
    "derive_outcome",
]
