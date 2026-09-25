"""One test's phase-resolved outcome, its decomposed identity, and its
catalogue entry.

Stdlib dataclasses -- no Pydantic, no ORM, no third-party validation,
matching `execution.py`. Naming avoids ``Test*`` on purpose: pytest would
collect ``TestResult`` or ``TestCase`` as a test class and warn on every run.

``OUTCOMES`` is a module-level ``frozenset``, never an ``Enum`` --
``class X(str, Enum)`` changes ``__format__`` between supported Python
versions.

The forbidden idiom throughout this module is ``x or None``: it turns a
genuine ``0.0`` duration or a genuine ``""`` parameter id into ``None``,
confusing "absent" with "empty".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

OUTCOMES = frozenset({"passed", "failed", "error", "skipped", "xfailed", "xpassed"})
"""The six outcome values `result.outcome`'s CHECK constraint accepts."""


@dataclass(frozen=True, slots=True)
class CaseIdentity:
    """A test's decomposed identity.

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
    """What a failed or errored result additionally records.

    ``None``/``False`` in every field means no failure evidence was captured;
    that shape is carried as `Result.failure is None`, never as a
    `FailureEvidence` full of nulls. Nested on `Result` rather than flattened
    into thirteen more fields, like `Execution.vcs`.
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

    def is_empty(self) -> bool:
        """True when every field is null or false: no evidence, which every
        layer stores and returns as `Result.failure is None`. A truncation
        flag alone counts as evidence -- it records that a field existed and
        was dropped."""
        return (
            self.failure_type is None
            and self.failure_message is None
            and not self.failure_message_truncated
            and self.failure_path is None
            and self.failure_lineno is None
            and self.failure_repr is None
            and not self.failure_repr_truncated
            and self.traceback is None
            and not self.traceback_truncated
            and self.skip_reason is None
            and not self.skip_reason_truncated
            and self.xfail_reason is None
            and not self.xfail_reason_truncated
        )


@dataclass(frozen=True, slots=True)
class CapturedOutput:
    """A result's captured stdout/stderr.

    ``stdout``/``stderr`` are `None` when never captured (e.g. `-s`) and
    `""` when captured and empty. Unlike `FailureEvidence`,
    `Result.captured` is never `None`: whether output was captured already
    lives in the `str | None` fields, so collapsing an all-null
    `CapturedOutput` to `None` would put the same fact in two places.
    """

    stdout: str | None
    stdout_truncated: bool
    stderr: str | None
    stderr_truncated: bool


@dataclass(frozen=True, slots=True)
class Result:
    """One test's resolved outcome for one run.

    ``outcome`` is the derived, overall verdict; the three ``*_outcome``
    fields are the per-phase verdicts the derivation was computed from, kept
    so the derivation is auditable rather than trusted. A phase that never
    ran stores `None` for both its outcome and its duration -- never `0.0`.

    ``failure`` is `None` when the result carries no failure evidence at
    all. ``captured`` is never `None` (see `CapturedOutput`).

    Both default to the "no evidence captured" shape. A default is not a
    claim that no failure happened, only that the constructing code supplied
    no evidence.
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
    """A test's catalogue row.

    ``last_seen_at`` advances monotonically at the storage layer; this
    dataclass carries whatever the store read back and does not enforce
    that invariant itself.
    """

    identity: CaseIdentity
    first_seen_at: datetime
    last_seen_at: datetime
    last_seen_run_id: str | None
