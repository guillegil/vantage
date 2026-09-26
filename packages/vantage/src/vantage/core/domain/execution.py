"""One pytest invocation, and its identifier.

Stdlib dataclasses -- no Pydantic, no ORM, no third-party validation.
Naming avoids ``Test*`` on purpose: pytest would collect ``TestExecution`` as
a test class and warn on every run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

IDENTITY_PATTERN = r"^[0-9a-f]{32}$"
"""A run identifier's shape: 32 lowercase hex characters, a dashless `uuid4`.
Text rather than a compiled pattern, so the service can hand the same value
to FastAPI and Pydantic as their `pattern`."""

_IDENTITY_RE = re.compile(IDENTITY_PATTERN)


@dataclass(frozen=True, slots=True)
class Identity:
    """A run identifier: 32 lowercase hex characters, a dashless `uuid4`."""

    value: str

    def __post_init__(self) -> None:
        if not _IDENTITY_RE.fullmatch(self.value):
            raise ValueError(f"Identity must be 32 lowercase hex characters, got {self.value!r}")


@dataclass(frozen=True, slots=True)
class VcsContext:
    """The repository state a session was recorded against.

    `commit_subject_truncated` travels WITH `commit_subject`, never
    independently -- the same rule the SQLite adapter's conflict branch
    encodes as a `CASE` keyed to whether the incoming subject is non-null.
    """

    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None
    root: str | None

    def is_empty(self) -> bool:
        """True when no value is known: the context of a run recorded
        outside a repository, which every layer stores and returns as
        `None` rather than as this object. The truncation flag does not
        count -- it only describes a subject, and there is none."""
        return (
            self.commit is None
            and self.branch is None
            and self.commit_subject is None
            and self.dirty is None
            and self.root is None
        )


@dataclass(frozen=True, slots=True)
class Execution:
    """One pytest invocation, as reported by the plugin."""

    identity: Identity
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None
    vcs: VcsContext | None = None
    """`None` when no repository state was captured."""
