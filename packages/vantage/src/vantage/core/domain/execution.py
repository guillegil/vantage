"""One pytest invocation, and its identifier.

Stdlib dataclasses (RQ-26) -- no Pydantic, no ORM, no third-party validation.
Naming avoids ``Test*`` on purpose: pytest would collect ``TestExecution`` as
a test class and warn on every run (CLAUDE.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_IDENTITY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class Identity:
    """A run identifier: 32 lowercase hex characters, a dashless `uuid4`."""

    value: str

    def __post_init__(self) -> None:
        if not _IDENTITY_PATTERN.fullmatch(self.value):
            raise ValueError(f"Identity must be 32 lowercase hex characters, got {self.value!r}")


@dataclass(frozen=True, slots=True)
class VcsContext:
    """The repository state a session was recorded against (design.md D47, D48).

    `commit_subject_truncated` travels WITH `commit_subject`, never
    independently -- the same rule the storage adapters' conflict branch
    encodes as a `CASE` keyed to whether the incoming subject is non-null
    (design.md D48).
    """

    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None
    root: str | None

    def merged_over(self, previous: VcsContext | None) -> VcsContext:
        """Per-FIELD coalesce: null -> value only, never value -> null.

        `self` is the incoming (just-reported) snapshot, `previous` is what
        is already stored -- the in-memory mirror of the storage adapters'
        per-column SQL `COALESCE(excluded.vcs_*, run.vcs_*)` (design.md D48).
        A partial incoming snapshot -- a detached HEAD, a repository with no
        commits -- must not null a fuller previous one field by field, which
        is exactly the bug class `stored.vcs if execution.vcs is None else
        execution.vcs` (whole-object coalesce) cannot express.
        """
        if previous is None:
            return self
        return VcsContext(
            commit=self.commit if self.commit is not None else previous.commit,
            branch=self.branch if self.branch is not None else previous.branch,
            commit_subject=self.commit_subject
            if self.commit_subject is not None
            else previous.commit_subject,
            commit_subject_truncated=self.commit_subject_truncated
            if self.commit_subject is not None
            else previous.commit_subject_truncated,
            dirty=self.dirty if self.dirty is not None else previous.dirty,
            root=self.root if self.root is not None else previous.root,
        )


@dataclass(frozen=True, slots=True)
class Execution:
    """One pytest invocation, as reported by the plugin (design.md, D1)."""

    identity: Identity
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None
    vcs: VcsContext | None = None
    """Appended with a default (design.md D48, tasks.md 3.6) so every
    pre-existing construction site keeps working unmodified."""
