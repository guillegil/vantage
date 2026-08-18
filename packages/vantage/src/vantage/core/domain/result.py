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

from dataclasses import dataclass
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
class Result:
    """One test's resolved outcome for one run (design.md, D17).

    ``outcome`` is the derived, overall verdict; the three ``*_outcome``
    fields are the per-phase verdicts the derivation was computed from, kept
    so the derivation is auditable rather than trusted. A phase that never
    ran stores `None` for both its outcome and its duration -- never `0.0`.
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
