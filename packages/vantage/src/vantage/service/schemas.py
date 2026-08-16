"""Pydantic v2 models for the ingestion boundary (design.md D1, D5).

``vantage.service`` is the one package RQ-24 does not constrain, so this is
where Pydantic v2 belongs -- at the system boundary, per CLAUDE.md's
validation rule. The validated model is converted to the core's ``Execution``
dataclass before the storage port is ever touched, so no Pydantic type
crosses into ``vantage.core`` (RQ-26, RQ-30.2).

**`extra=` is asymmetric between `RunReport` and `SessionReport`, and that
asymmetry is deliberate -- it is the whole point of the HTTP boundary.**
``RunReport`` (the ``run`` section) is ``extra="forbid"``. The envelope,
``SessionReport``, is ``extra="ignore"``. That looks inconsistent; it is not.
Across a versioned HTTP boundary the two directions of version skew have
different costs (ADR-4, ADR-9):

- An unknown field **inside** ``run`` means the client and server disagree
  about what a run *is*. That is a client bug -- the plugin is either newer
  than this server understands or malformed -- and rejecting it loudly is a
  service to whoever wrote it. Silently accepting an unrecognised run field
  would let a typo or a schema drift pass unnoticed forever.
- An unknown **section** on the envelope means a newer ``pytest-vantage`` is
  talking to an older ``vantage`` -- Milestone 2 adds ``"results"``,
  Milestone 3 adds ``"environment"`` and ``"vcs"``, as sibling sections. That
  is an ordinary, expected, supported state, not an error: it is what lets
  the two distributions release independently at all (ADR-4). Ignoring it is
  the mechanism, not a shortcut.

If both were strict, every plugin upgrade would be a breaking change for
every server that has not yet been upgraded to match. If both ignored extras,
a typo'd or drifted run field would be silently swallowed instead of
rejected, which is exactly what RQ-42 exists to prevent. The asymmetry is not
an inconsistency to "fix" -- it is two different version-skew directions with
two different acceptable costs.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"


class RunReport(BaseModel):
    """The ``run`` section of a session report (design.md D1).

    ``extra="forbid"``: every field is named here, and nothing else is
    accepted. See the module docstring for why this differs from
    `SessionReport`. Every field is required and unaliased with no default --
    even the client's own nulls (`finished_at`, `interrupt_reason`) must be
    sent explicitly, so a field the client forgot to send is a rejection, not
    a silently substituted default.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_IDENTITY_PATTERN)
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None


class SessionReport(BaseModel):
    """The envelope submitted to `POST /api/v1/runs` (design.md D1).

    ``extra="ignore"``: see the module docstring. Milestone 2 and 3 add
    sibling sections (``results``, ``environment``, ``vcs``) that an older
    server must tolerate rather than reject.
    """

    model_config = ConfigDict(extra="ignore")

    run: RunReport


class Acknowledgement(BaseModel):
    """The response body for both `201` and `200` (design.md D3)."""

    run_id: str
    status: str
    ignored: list[str] = []
