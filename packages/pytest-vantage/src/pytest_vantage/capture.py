"""Plugin-local decomposition of the pytest node id (design.md D18, RQ-9),
and -- from Phase 7 -- phase accumulation, outcome derivation and result
payload assembly (design.md D16, D17).

`vantage.core.domain.result.CaseIdentity`/`Result` are the shapes this
information eventually takes, but `pytest-vantage` cannot import `vantage`
(RQ-24) -- this module owns plain stdlib structures of its own instead.
Standard library and `pytest` only -- never `xdist` (`test_plugin_imports.py`
is the guard); `pytest` itself is this distribution's one declared
dependency, and every report `Recorder` hands here IS a `pytest.TestReport`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

import pytest


class DecomposedIdentity(NamedTuple):
    """A test's identity, decomposed from its pytest node id.

    ``class_name`` is `None` for a module-level test, never `""` (RQ-9.2).
    ``param_id`` is `None` for an unparametrised test and `""` for a
    parametrised test whose parameter id is itself the empty string -- the
    brackets are the evidence of parametrisation, not their content
    (RQ-9.3, design.md D18). The forbidden idiom here is ``x or None``: it
    would turn a genuine ``""`` into ``None`` and erase that distinction.
    """

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None


def decompose(node_id: str) -> DecomposedIdentity:
    """Split a pytest node id into its identity components (design.md D18).

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
       is not a real pytest node id (every one has a file path and at
       least one test-path segment) and raises ``ValueError`` rather than
       silently inventing a function name.
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
        raise ValueError(
            f"not a pytest node id (missing '::' between file path and test path): {node_id!r}"
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
    `isoformat_utc` exactly (design.md D1) -- duplicated here rather than
    imported, since `recorder.py` imports FROM this module and the reverse
    would be circular.
    """
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


class _Pending:
    """Accumulates the up-to-three reports pytest emits for one test's
    lifecycle, keyed by node id in `Recorder._results` (design.md D16). A
    `dict[str, _Pending]` gives insertion order for free, and a duplicate
    report for the same node id and phase overwrites rather than duplicates
    (D19 layer 1's supporting mechanism).
    """

    __slots__ = ("setup", "call", "teardown")

    def __init__(self) -> None:
        self.setup: pytest.TestReport | None = None
        self.call: pytest.TestReport | None = None
        self.teardown: pytest.TestReport | None = None

    def record(self, report: pytest.TestReport) -> None:
        setattr(self, report.when, report)


def accumulate(pending: dict[str, _Pending], report: pytest.TestReport) -> None:
    """Record one phase report into `pending`, keyed by `report.nodeid`
    (design.md D16). Called from `Recorder.pytest_runtest_logreport`, once
    per phase report pytest fires -- never sends anything itself."""
    pending.setdefault(report.nodeid, _Pending()).record(report)


def derive_outcome(
    setup: pytest.TestReport, call: pytest.TestReport | None, teardown: pytest.TestReport
) -> str:
    """Derive the overall outcome from the three phase reports, in design.md
    D17's fixed nine-row precedence order. `call` is `None` only when
    `setup.outcome` is not `"passed"` (pytest never runs call after a failed
    or skipped setup).

    Two consequences easy to get wrong: a teardown failure downgrades ONLY a
    `passed` call result (row 8) -- every other verdict keeps its own word.
    A **strict** `xfail` that passes has `outcome="failed"` with `wasxfail`
    entirely ABSENT (verified against `_pytest/skipping.py`), so row 7
    cannot fire for it and it falls through to row 6 (`failed`), not
    `xpassed`. `wasxfail`'s PRESENCE (`hasattr`), never its truthiness, is
    what matters -- pytest sometimes sets the reason to `""`.
    """
    if setup.outcome == "failed":
        return "error"
    if setup.outcome == "skipped":
        return "skipped"
    if call is None:
        raise AssertionError("setup passed but no call report was recorded (design.md D17)")
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
    """`None` when the phase never ran, never `0.0` (design.md D17, RQ-5.2).
    Plain attribute access, never `report.duration or None`, which would
    turn a genuine `0.0` into `None`.
    """
    if report is None:
        return None
    return report.duration


def _phase_timestamp(report: pytest.TestReport, attribute: str) -> str | None:
    """`getattr(report, "start"/"stop", None)` -- epoch floats on pytest
    >= 8 (design.md D16) -- via `datetime.fromtimestamp(..., timezone.utc)`.
    Never `datetime.UTC` (3.11+, above this project's 3.10 floor).
    """
    epoch = getattr(report, attribute, None)
    if epoch is None:
        return None
    return _isoformat_utc(datetime.fromtimestamp(epoch, timezone.utc))


def _worker_id(report: pytest.TestReport) -> str | None:
    """A `getattr` chain, never an xdist import (RQ-24, design.md D16):
    `report.worker_id` first, then `report.node.gateway.id`. `None` when
    neither is present.
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
    """Which phase report's `vantage_evidence` (design.md D68) belongs in
    the recorded result -- design.md D69's phase-precedence table, keyed
    off the DERIVED outcome, never restated as an independent condition:

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
    return None  # xpassed, passed -- design.md D69


def build_result(node_id: str, pending: _Pending) -> dict[str, object] | None:
    """Build one wire-shape `results[]` entry from an accumulated `_Pending`
    (design.md D16-D18). Returns `None` -- dropped, never invented -- when
    the teardown report was never seen: a half-observed test (e.g. one
    interrupted mid-call) is worse reported as whole than not at all
    ("resolution, not attendance", D16).
    """
    if pending.teardown is None:
        return None
    setup = pending.setup
    if setup is None:
        raise AssertionError("a teardown report implies a setup report was seen first")
    call = pending.call
    teardown = pending.teardown

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

    # design.md D69: the evidence `EvidenceCollector` attached to whichever
    # phase report D69's precedence table selects, merged in -- absent
    # entirely (never a dict of nulls) when that phase carries none, e.g. a
    # session with `EvidenceCollector` never registered (the opt-out, D72).
    evidence_report = _select_evidence_phase(setup, call, teardown, outcome)
    evidence = getattr(evidence_report, "vantage_evidence", None)
    if isinstance(evidence, dict):
        result.update(evidence)

    return result


def assemble_results(pending: dict[str, _Pending]) -> list[dict[str, object]]:
    """Build the `results` array in insertion (execution) order (design.md
    D16). Entries with no teardown report are dropped (`build_result`
    returning `None`).
    """
    results: list[dict[str, object]] = []
    for entry_node_id, entry in pending.items():
        result = build_result(entry_node_id, entry)
        if result is not None:
            results.append(result)
    return results


__all__ = [
    "DecomposedIdentity",
    "accumulate",
    "assemble_results",
    "build_result",
    "decompose",
    "derive_outcome",
]
