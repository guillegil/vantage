"""Node-id decomposition, phase accumulation, outcome derivation and result
payload assembly for the plugin.

`vantage.core.domain.result.CaseIdentity`/`Result` are the shapes this
information eventually takes, but the plugin must stay installable without
the server, so it cannot import `vantage` and owns plain stdlib structures
instead. Standard library and `pytest` only -- never `xdist`
(`test_plugin_imports.py` is the guard).
"""

from __future__ import annotations

from collections.abc import Mapping
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


def isoformat_utc(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC text: `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`,
    the same text the server stores.

    Converted to UTC first, whatever offset `moment` carries, and always
    with six fractional digits -- plain `isoformat()` drops them when they
    are zero -- so lexicographic order is chronological order. Not
    `strftime`, whose `%Y` leaves a year below 1000 unpadded on glibc.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


# The only outcomes pytest itself gives a phase report. A rerun plugin logs an
# intermediate attempt as "rerun"; that is not a result, and the server
# rejects any phase outcome outside its vocabulary.
_REPORT_OUTCOMES = frozenset({"passed", "failed", "skipped"})

_PHASES = frozenset({"setup", "call", "teardown"})


# How much each verdict says went wrong, for choosing between several
# complete executions of one node id.
_SEVERITY = {"skipped": 0, "xfailed": 1, "passed": 2, "xpassed": 3, "failed": 4, "error": 5}


def is_subtest(report: pytest.TestReport) -> bool:
    """Whether `report` is one subtest's report (pytest's `subtests` fixture,
    unittest's `subTest`, or the pytest-subtests plugin on pytest 8). It
    carries the parent test's node id and `when="call"`, and is told apart
    by the subtest `context` it carries -- duck-typed, so no private pytest
    module is imported.
    """
    return report.when == "call" and getattr(report, "context", None) is not None


def is_crash_report(report: pytest.TestReport) -> bool:
    """Whether `report` says the test's process died while running it.

    xdist logs a test whose worker crashed (a segfault, an OOM kill,
    `os._exit`) once, on the controller, with `when="???"` and outcome
    `failed`, then replaces the worker. It is no phase of the test, but it
    is pytest's verdict on it: pytest counts the test as failed.
    """
    return report.when not in _PHASES and report.outcome == "failed"


class _Execution:
    """The reports pytest emits for one execution of a test: one attempt, on
    one worker. A duplicate report for the same phase overwrites rather than
    duplicates, so a report delivered twice never becomes a second result.

    Subtest reports are kept apart, in arrival order. They arrive before the
    parent's own call report, so storing them as `call` would let the
    parent's report overwrite every subtest verdict. So is a crash report
    (`is_crash_report`), which ends the execution whatever phases were seen.
    """

    __slots__ = ("setup", "call", "teardown", "subtests", "crash")

    def __init__(self) -> None:
        self.setup: pytest.TestReport | None = None
        self.call: pytest.TestReport | None = None
        self.teardown: pytest.TestReport | None = None
        self.subtests: list[pytest.TestReport] = []
        self.crash: pytest.TestReport | None = None

    def record(self, report: pytest.TestReport) -> None:
        """Explicit dispatch on the three phases, plus the crash report.
        Any other report of no known phase is ignored.
        """
        when = report.when
        if when == "setup":
            # A setup report starts a new attempt at the test (a rerun plugin
            # retrying it), so nothing from an earlier attempt may survive.
            self.setup = self.call = self.teardown = self.crash = None
            self.subtests = []
        if is_crash_report(report):
            self.crash = report
            return
        if report.outcome not in _REPORT_OUTCOMES:
            return
        if when == "setup":
            self.setup = report
        elif is_subtest(report):
            self.subtests.append(report)
        elif when == "call":
            self.call = report
        elif when == "teardown":
            self.teardown = report


class PendingResult:
    """Every report pytest emitted for one node id, keyed by node id in
    `Recorder._results`; a `dict[str, PendingResult]` gives insertion order
    for free.

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


def accumulate(pending: dict[str, PendingResult], report: pytest.TestReport) -> None:
    """Record one phase report into `pending`, keyed by `report.nodeid`.
    Called from `Recorder.pytest_runtest_logreport`, once per phase report
    pytest fires -- never sends anything itself."""
    pending.setdefault(report.nodeid, PendingResult()).record(report)


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
    An xfail raised during setup (`xfail(run=False)`, `pytest.xfail()` in a
    fixture, an xfail-marked test whose fixture raises) arrives as a skipped
    setup carrying `wasxfail`, and pytest counts it as xfailed.
    """
    if setup.outcome == "failed":
        return "error"
    if setup.outcome == "skipped":
        return "xfailed" if hasattr(setup, "wasxfail") else "skipped"
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


def _phase_timestamp(report: pytest.TestReport | None, attribute: str) -> str | None:
    """`getattr(report, "start"/"stop", None)` -- epoch floats on pytest
    >= 8 -- via `datetime.fromtimestamp(..., timezone.utc)`. Never
    `datetime.UTC`, which needs 3.11 and this project supports 3.10.
    `None` when the phase never ran.
    """
    epoch = getattr(report, attribute, None)
    if epoch is None:
        return None
    return isoformat_utc(datetime.fromtimestamp(epoch, timezone.utc))


def _worker_id(report: pytest.TestReport | None) -> str | None:
    """A `getattr` chain, never an xdist import, so the plugin works with
    xdist absent: `report.worker_id` first, then `report.node.gateway.id`
    (the only one a crash report carries). `None` when neither is present.
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

    | Derived outcome      | Evidence taken from                          |
    | -------------------- | --------------------------------------------- |
    | `error`              | setup if setup failed, else teardown          |
    | `failed`             | call                                          |
    | `skipped`, `xfailed` | setup if setup skipped, else call             |
    | `xpassed`, `passed`  | none                                          |

    `call` is `None` only when `setup.outcome` is not `"passed"`
    (`derive_outcome`'s own precondition) -- exactly the cases this
    function never needs `call` for (`error`, or `skipped`/`xfailed` with a
    skipped setup), so the `pytest.TestReport | None` signature never needs
    a runtime `None` check.
    """
    if outcome == "error":
        return setup if setup.outcome == "failed" else teardown
    if outcome == "failed":
        return call
    if outcome in ("skipped", "xfailed"):
        return setup if setup.outcome == "skipped" else call
    return None  # xpassed, passed


def _captured_output(reports: list[pytest.TestReport]) -> dict[str, object]:
    """`captured_stdout`/`captured_stderr`, concatenated across every phase
    that ran, in setup -> subtests -> call -> teardown order, with no
    delimiter (an in-band phase header could be forged by a test printing
    that exact line). Output printed inside a `subtests` block is captured
    on that subtest's own report, not on the call report.
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
        for report in reports
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


def _describes_a_failure(evidence: object) -> bool:
    return isinstance(evidence, dict) and any(
        evidence.get(field) is not None
        for field in ("failure_type", "failure_message", "traceback")
    )


def build_result(node_id: str, pending: PendingResult) -> dict[str, object] | None:
    """Build one wire-shape `results[]` entry from an accumulated
    `PendingResult`. Returns `None` -- dropped, never invented -- when no
    execution of the test was observed whole: a half-observed test (e.g. one
    interrupted mid-call) is worse reported as whole than not at all. The
    exception is a test whose process died under it, which pytest itself
    reports as failed (`is_crash_report`).

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
    """One execution's `results[]` entry, or `None` when there is no verdict
    to record: its teardown report was never seen, or setup passed and the
    test itself never ran (`--setup-only`, `--setup-plan`).

    A crash report is a verdict on its own: the test failed, whatever phases
    were seen before its process died, and the crash report carries its
    evidence. A phase never seen is recorded as never run.
    """
    setup = execution.setup
    call = execution.call
    teardown = execution.teardown
    crash = execution.crash
    failed_subtests = [report for report in execution.subtests if report.outcome == "failed"]
    evidence_report: pytest.TestReport | None
    if crash is not None:
        outcome = "failed"
        evidence_report = crash
    else:
        if teardown is None:
            return None
        if setup is None:
            raise AssertionError("a teardown report implies a setup report was seen first")
        if call is None and setup.outcome == "passed":
            return None
        outcome = derive_outcome(setup, call, teardown)
        if outcome == "passed" and failed_subtests:
            # pytest's own rule: a test that passed but contains a failed
            # subtest is failed. pytest applies it to the report for the
            # `subtests` fixture but not for unittest's `subTest`, whose
            # failures still fail the session, so it is applied here to both.
            outcome = "failed"
        evidence_report = _select_evidence_phase(setup, call, teardown, outcome)

    identity = decompose(node_id)
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
        "setup_outcome": setup.outcome if setup is not None else None,
        "call_outcome": call.outcome if call is not None else None,
        "teardown_outcome": teardown.outcome if teardown is not None else None,
        "setup_duration": setup_duration,
        "call_duration": call_duration,
        "teardown_duration": teardown_duration,
        "worker_id": _worker_id(setup if setup is not None else crash),
    }

    # The evidence `EvidenceCollector` attached to the selected report,
    # merged in -- absent entirely (never a dict of nulls) when that report
    # carries none, e.g. without `--vantage-failure-text`.
    evidence = getattr(evidence_report, "vantage_evidence", None)
    if outcome == "failed" and failed_subtests and not _describes_a_failure(evidence):
        # Failed through its subtests: their exceptions were caught, so the
        # call report has none to describe. The first failing subtest's is
        # the evidence.
        evidence = getattr(failed_subtests[0], "vantage_evidence", None)
    if isinstance(evidence, dict):
        result.update(evidence)

    # Captured output spans ALL phases, not just the one selected above --
    # applied after the evidence merge so it overrides the single-phase
    # captured_stdout/captured_stderr that merge carried in.
    phase_reports = [setup, *execution.subtests, call, teardown, crash]
    result.update(_captured_output([report for report in phase_reports if report is not None]))

    return result


def collection_error_result(report: pytest.CollectReport) -> dict[str, object]:
    """One `results[]` entry for a collector pytest could not collect -- a
    module that fails to import, a class whose collection raised. pytest
    counts it as an error, so it is recorded as one, under the collector's
    own node id, as pytest's JUnit XML records it: a run with a broken
    module then never reads as all passing.

    No phase of any test ran, so every phase, duration and timestamp is
    null. `worker_id` is null too: under xdist every worker collects every
    module, and the controller is handed the failure once, from whichever
    worker reported it first. Its evidence is pytest's collection text,
    attached by `EvidenceCollector` only when failure text was asked for.
    """
    identity = decompose(report.nodeid)
    result: dict[str, object] = {
        "node_id": identity.node_id,
        "file_path": identity.file_path,
        "class_name": identity.class_name,
        "function_name": identity.function_name,
        "param_id": identity.param_id,
        "outcome": "error",
        "duration": None,
        "started_at": None,
        "finished_at": None,
        "setup_outcome": None,
        "call_outcome": None,
        "teardown_outcome": None,
        "setup_duration": None,
        "call_duration": None,
        "teardown_duration": None,
        "worker_id": None,
    }
    evidence = getattr(report, "vantage_evidence", None)
    if isinstance(evidence, dict):
        result.update(evidence)
    return result


class AssembledResults(list[dict[str, object]]):
    """The `results` array, plus `dropped`: how many tests had reports that
    could not be built into a result. Still a plain list to everything that
    serialises or budgets it.
    """

    dropped: int = 0


def assemble_results(
    pending: dict[str, PendingResult],
    *,
    collection_errors: Mapping[str, pytest.CollectReport] | None = None,
) -> AssembledResults:
    """Build the `results` array in insertion (execution) order, then one
    `error` result per collector that failed (`collection_error_result`).
    Entries that were never observed whole are left out (`build_result`
    returning `None`), and so is a collection error under a node id a test
    result already has: the server rejects a report naming one node id
    twice, with every result in it.

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
    recorded = {result["node_id"] for result in results}
    for node_id, report in (collection_errors or {}).items():
        if node_id in recorded:
            continue
        try:
            results.append(collection_error_result(report))
        except Exception:  # deliberately broad, same reason
            results.dropped += 1
    return results


__all__ = [
    "AssembledResults",
    "DecomposedIdentity",
    "PendingResult",
    "accumulate",
    "assemble_results",
    "build_result",
    "collection_error_result",
    "decompose",
    "derive_outcome",
    "is_crash_report",
    "is_subtest",
    "isoformat_utc",
]
