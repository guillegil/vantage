"""One test's phase-resolved outcome, its decomposed identity, and its
catalogue entry.

Stdlib dataclasses (RQ-26) -- no Pydantic, no ORM, no third-party
validation, matching `execution.py`. Naming avoids ``Test*`` on purpose:
pytest would collect ``TestResult`` or ``TestCase`` as a test class and warn
on every run (CLAUDE.md).

``OUTCOMES`` is a module-level ``frozenset``, never an ``Enum`` --
``class X(str, Enum)`` changes ``__format__`` between Python 3.10 and 3.11,
and this project supports both (design.md, D21).

The forbidden idiom throughout this module is ``x or None``: it turns a
genuine ``0.0`` duration or a genuine ``""`` parameter id into ``None``,
which is exactly the absent-versus-empty confusion RQ-5.2 and RQ-9.2/9.3
exist to prevent (design.md, D17-D18).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

OUTCOMES = frozenset({"passed", "failed", "error", "skipped", "xfailed", "xpassed"})
"""The six outcome values `result.outcome`'s CHECK constraint accepts."""


@dataclass(frozen=True, slots=True)
class CaseIdentity:
    """A test's decomposed identity (design.md, D18).

    ``class_name`` is `None` for a module-level test, never `""`.
    ``param_id`` is `None` for an unparametrised test and `""` for a
    parametrised test whose parameter id is itself the empty string --
    the brackets are the evidence of parametrisation, not their content.
    """

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    """What a failed or errored result additionally records (design.md D69,
    D77). ``None``/``False`` in every field means no failure evidence was
    captured -- `Result.failure` normalises that all-null shape to `None`
    (D48's rule, inherited), so a bare `FailureEvidence` with everything
    unset is never constructed directly by a caller building a `Result`.

    Nested on `Result` rather than flattened, following `Execution.vcs`
    (the house pattern) instead of adding thirteen flat fields to a
    dataclass that already has twelve.
    """

    failure_type: str | None
    failure_message: str | None
    failure_message_truncated: bool
    failure_path: str | None
    failure_lineno: int | None
    failure_repr: str | None
    failure_repr_truncated: bool
    traceback: str | None
    traceback_truncated: bool
    skip_reason: str | None
    skip_reason_truncated: bool
    xfail_reason: str | None
    xfail_reason_truncated: bool


@dataclass(frozen=True, slots=True)
class CapturedOutput:
    """A result's captured stdout/stderr (design.md D71, D77).

    ``stdout``/``stderr`` are `None` when never captured (e.g. `-s`) and
    `""` when captured and empty -- the empty-versus-absent distinction the
    `failure-evidence` capability requires. Unlike `FailureEvidence`,
    `Result.captured` is never `None`: that distinction lives INSIDE this
    type, in the `str | None` fields, so collapsing an all-null
    `CapturedOutput` to `None` would put the same fact in two places.
    """

    stdout: str | None
    stdout_truncated: bool
    stderr: str | None
    stderr_truncated: bool


@dataclass(frozen=True, slots=True)
class Result:
    """One test's resolved outcome for one run (design.md, D17).

    ``outcome`` is the derived, overall verdict; the three ``*_outcome``
    fields are the per-phase verdicts the derivation was computed from, kept
    so the derivation is auditable rather than trusted. A phase that never
    ran stores `None` for both its outcome and its duration -- never `0.0`.

    ``failure`` is `None` when the result carries no failure evidence at all
    -- a failure either happened or it did not (design.md D77). ``captured``
    is never `None` (see `CapturedOutput`).

    ``failure``/``captured`` default to the "no evidence captured" shape so
    every `Result` constructed before this change's later phases wire
    failure evidence through -- ``sqlite_store.py``, ``routes/runs.py``,
    ``vantage_port_contract.py`` and ``scripts/measure_history_latency.py``
    -- keeps compiling and passing without modification. Phase 6/7 of this
    change replace these defaults with real values at each of those call
    sites; the defaults themselves are never a claim that a failure did not
    happen, only that this `Result` was built by code that does not yet know
    about failure evidence.
    """

    identity: CaseIdentity
    outcome: str
    duration: float | None
    started_at: datetime | None
    finished_at: datetime | None
    setup_outcome: str | None
    call_outcome: str | None
    teardown_outcome: str | None
    setup_duration: float | None
    call_duration: float | None
    teardown_duration: float | None
    worker_id: str | None
    failure: FailureEvidence | None = None
    captured: CapturedOutput = field(
        default_factory=lambda: CapturedOutput(
            stdout=None, stdout_truncated=False, stderr=None, stderr_truncated=False
        )
    )

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}, got {self.outcome!r}")


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """A test's catalogue row (design.md, D20, RQ-13).

    ``last_seen_at`` advances monotonically at the storage layer; this
    dataclass carries whatever the store read back and does not enforce
    that invariant itself.
    """

    identity: CaseIdentity
    first_seen_at: datetime
    last_seen_at: datetime
    last_seen_run_id: str | None
