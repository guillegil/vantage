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
class Execution:
    """One pytest invocation, as reported by the plugin (design.md, D1)."""

    identity: Identity
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None
