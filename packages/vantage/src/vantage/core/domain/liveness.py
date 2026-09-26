"""Abandonment derivation: presenting a run as running, finished, interrupted
or abandoned from its stored state alone.

Every input is an existing column, and the return value is never persisted.

``PRESENTATIONS`` is a module-level ``frozenset`` of plain ``str``, never an
``Enum``: ``class X(str, Enum)`` changes ``__format__`` between interpreter
versions -- ``f"{X.A}"`` is ``'abandoned'`` on Python 3.10 and ``'X.A'`` on
Python 3.13, inside the supported range.
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
    """Derive `execution`'s presentation, in this fixed precedence:

    1. `finished_at is not None` -> "finished". An orderly end is an end.
    2. `interrupted`, or any recorded exit status -> "interrupted". A finish
       report *did* arrive, so the run is never abandoned however stale its
       last contact -- checked before the clock, and not shadowed by rule 1
       because an interrupted run carries `finished_at is None`.
    3. `now - (last_contact_at or execution.started_at) > grace` ->
       "abandoned". The `started_at` fallback is defensive: every row this
       code writes has a last contact, but a row written by another adapter
       or edited by hand might not.
    4. otherwise -> "running".
    """
    if execution.finished_at is not None:
        return "finished"
    if execution.interrupted or execution.exit_status is not None:
        # The plugin sends a null `finished_at` both for an interruption --
        # Ctrl-C or `pytest.exit`, whatever the exit status -- and for
        # pytest's internal error (status 3), but sets `interrupted` only for
        # the first, so keying on `interrupted` alone would present a session
        # that did report as one that never did. pytest's own stops with
        # status 2 carry a `finished_at` and read as finished.
        return "interrupted"
    reference = last_contact_at if last_contact_at is not None else execution.started_at
    if now - reference > grace:
        return "abandoned"
    return "running"


__all__ = ["PRESENTATIONS", "derive_presentation"]
