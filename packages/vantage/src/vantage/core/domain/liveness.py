"""Abandonment derivation: presenting a run as running, finished, interrupted
or abandoned from its stored state alone (design.md D34, RQ-44).

A pure function with no caller yet -- `app.state.grace_period` (design.md
D34) is a named seam for the read API this write-side change does not add.
Nothing here invents a stored field (RQ-44.4): every input is a column that
already exists, and the return value is never persisted.

``PRESENTATIONS`` is a module-level ``frozenset``, never an ``Enum`` --
**corrected 2026-08-19**. This module first read ``class RunPresentation(str,
Enum)``, which caught the ``StrEnum`` trap (3.11+, above the 3.10 floor) and
walked into the one beside it: ``class X(str, Enum)`` changes ``__format__``
between interpreter versions. Measured on this project's own floor and
ceiling, ``f"{X.A}"`` is ``'abandoned'`` on Python 3.10 and ``'X.A'`` on
Python 3.13 -- same source, different string, inside the supported range.
``vantage.core.domain.result`` already records exactly this reason for
keeping ``OUTCOMES`` a ``frozenset`` and says "never an ``Enum``" in its own
docstring; this module follows the same precedent rather than inventing a
second shape for the same kind of vocabulary.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from vantage.core.domain.execution import Execution

PRESENTATIONS = frozenset({"finished", "interrupted", "abandoned", "running"})
"""The four values `derive_presentation` can return."""


def derive_presentation(
    execution: Execution,
    *,
    last_contact_at: datetime | None,
    now: datetime,
    grace: timedelta,
) -> str:
    """Derive `execution`'s presentation, in this fixed precedence (design.md
    D34):

    1. `finished_at is not None` -> "finished". An orderly end is an end.
    2. `interrupted` -> "interrupted" (RQ-44.3). A report *did* arrive, so it
       is never abandoned no matter how stale its last contact -- checked
       before the clock, and not shadowed by rule 1 because a Ctrl-C run
       carries `finished_at is None`.
    3. `now - (last_contact_at or execution.started_at) > grace` ->
       "abandoned". The `or execution.started_at` fallback is the defensive
       branch of D27 -- unreachable for any row this code writes, kept for a
       row written by another adapter or edited by hand.
    4. otherwise -> "running".
    """
    if execution.finished_at is not None:
        return "finished"
    if execution.interrupted:
        return "interrupted"
    reference = last_contact_at if last_contact_at is not None else execution.started_at
    if now - reference > grace:
        return "abandoned"
    return "running"


__all__ = ["PRESENTATIONS", "derive_presentation"]
